"""DBOS sync application: one scheduled workflow fans out to dump and publish queues."""

import asyncio
import threading
from asyncio import gather
from datetime import datetime

from dbos import (
    DBOS,
    DBOSConfig,
    ScheduleInput,
    SetWorkflowTimeout,
    WorkflowHandleAsync,
)

from .cache import clear_cache
from .constants import DUMP_QUEUE, PUBLISH_QUEUE, SYNC_QUEUE
from .duckdb import DuckDB
from .extraction import run_extraction
from .kubernetes import (
    api_client_factory,
    apps_factory,
    deployment_ready,
    expand_template,
    load_config,
)
from .log import logger, schemaname, tablename
from .metrics import metrics
from .models import (
    DumpFailure,
    DumpResult,
    DumpSuccess,
    DumpTask,
    PublicationResult,
    PublishedSchema,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
    SyncWork,
)
from .planning import run_planning
from .postgres import connect_pg
from .publication import configure_s3_secret, run_publication
from .replication import current_wal_lsn, replicas_replayed
from .s3 import clear_s3_bucket
from .schema import initialize_schemas, revoke_anonymous_access
from .settings import settings
from .state import (
    build_table_states,
    emit_error,
    ensure_app_schema,
    write_table_states,
)
from .utils import wait_for


@DBOS.step()
async def build_work(run_id: str) -> SyncWork:
    """Plan one run: detect changes and build dump tasks and schema plans."""
    async with (
        DuckDB.connect() as duckdb_conn,
        connect_pg(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn,
    ):
        await ensure_app_schema(pg_conn)
        return await run_planning(
            settings.sync_config,
            pg_conn,
            duckdb_conn,
            run_id,
            settings.S3_BUCKET,
        )


@DBOS.step()
async def record_run_status(status: str) -> None:
    """Record one sync run status metric."""
    metrics.sync_runs_total.add(1, {"status": status})


@DBOS.step()
async def record_dump_metrics(
    task_id: str, table: str, schema: str, status: str
) -> None:
    """Record dump task count metric."""
    metrics.dump_tasks_total.add(
        1, {"table": table, "schema": schema, "status": status}
    )
    logger.info("Dump completed task_id=%s status=%s", task_id, status)


@DBOS.step()
async def record_publish_metrics(result: PublicationResult, schema_name: str) -> None:
    """Record publication success and failure metrics."""
    success_count = len(result.published_tables)
    if success_count:
        metrics.publish_tables_total.add(
            success_count, {"schema": schema_name, "status": "success"}
        )

    failure_count = len(result.plan.signatures) - success_count
    if failure_count:
        metrics.publish_tables_total.add(
            failure_count, {"schema": schema_name, "status": "failure"}
        )


@DBOS.step()
async def seed(plans: list[SyncPlan]) -> None:
    """Initialize configured schemas and policy objects for one run."""
    by_dsn: dict[str, dict[str, SchemaConfig]] = {}

    for plan in plans:
        schema_name = plan.schema_name
        dsn = settings.SCHEMA_WRITERS.dsn(schema_name)
        by_dsn.setdefault(dsn, {})[schema_name] = (
            settings.sync_config.schemas[schema_name]
        )

    for dsn, schemas in by_dsn.items():
        async with connect_pg(dsn) as pg_conn:
            await initialize_schemas(
                pg_conn,
                SyncConfig(schemas=schemas),
            )

    for plan in plans:
        metrics.seed_runs_total.add(
            1, {"schema": plan.schema_name, "status": "success"}
        )


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.DUMP_QUEUE_MAX_ATTEMPTS,
    should_retry=lambda error: not isinstance(error, ValueError | RuntimeError),
)
async def extract(task: DumpTask) -> None:
    """Extract one dump task from BigQuery to Parquet."""
    async with DuckDB.connect() as duckdb_conn:
        await run_extraction(task, duckdb_conn)


@DBOS.step()
async def record_dump_failure(task: DumpTask, error: str) -> None:
    """Persist one dump failure in the dp.errors table."""
    async with connect_pg(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        await emit_error(
            pg_conn,
            "extraction_failed",
            task=task.task_id,
            table=task.table,
            error=error,
        )


@DBOS.step()
async def load_and_publish(plan: SyncPlan, failed_paths: set[str]) -> PublishedSchema:
    """Load and publish one schema plan, then capture the commit WAL position."""
    schemaname.set(plan.schema_name)
    config = SyncConfig(
        schemas={plan.schema_name: settings.sync_config.schemas[plan.schema_name]}
    )

    async with (
        connect_pg(settings.SCHEMA_WRITERS.dsn(plan.schema_name)) as pg_conn,
        connect_pg(settings.DBOS_SYSTEM_DATABASE_URL) as state_conn,
    ):
        await configure_s3_secret(pg_conn)

        result = await run_publication(pg_conn, state_conn, config, plan, failed_paths)
        target_lsn = await current_wal_lsn(pg_conn)

    return PublishedSchema(result=result, target_lsn=target_lsn)


@DBOS.step()
async def wait_replica(schema_name: str, target_lsn: str) -> None:
    """Wait until every replica has replayed the publication WAL position."""
    async with connect_pg(settings.SCHEMA_WRITERS.dsn(schema_name)) as pg_conn:
        await wait_for(
            lambda: replicas_replayed(pg_conn, target_lsn),
            timeout=settings.REPLICATION_WAIT_TIMEOUT_SECONDS,
            interval=settings.REPLICATION_POLL_INTERVAL_SECONDS,
            message="PostgreSQL standbys did not replay the publication WAL",
        )


@DBOS.step(retries_allowed=True, max_attempts=3)
async def refresh_schema(schema_name: str, run_id: str) -> None:
    """Restart both PostgREST deployments and wait for their rollouts."""
    load_config()

    async with api_client_factory() as api_client:
        apps = apps_factory(api_client)
        namespace = settings.KUBERNETES_NAMESPACE
        names = [
            expand_template(settings.POSTGREST_RO_DEPLOYMENT_TEMPLATE, schema_name),
            expand_template(settings.POSTGREST_RW_DEPLOYMENT_TEMPLATE, schema_name),
        ]
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"data-proxy.io/schema-cache-revision": run_id}
                    }
                }
            }
        }

        for name in names:
            await apps.patch_namespaced_deployment(
                name=name,
                namespace=namespace,
                body=patch,
            )

        async def wait_for_deployment(name: str) -> None:
            await wait_for(
                lambda: deployment_ready(
                    lambda: apps.read_namespaced_deployment(
                        name=name, namespace=namespace
                    )
                ),
                timeout=settings.POSTGREST_RO_ROLLOUT_TIMEOUT_SECONDS,
                interval=2,
                message=f"PostGREST rollout did not become ready: {name}",
            )

        await gather(*(wait_for_deployment(name) for name in names))


@DBOS.step()
async def commit_state(plan: SyncPlan, result: PublicationResult) -> None:
    """Persist committed table state for one published schema."""
    config = SyncConfig(
        schemas={plan.schema_name: settings.sync_config.schemas[plan.schema_name]}
    )
    states = build_table_states(result, config)
    async with connect_pg(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        await write_table_states(pg_conn, states)


@DBOS.step()
async def finalize(run_id: str) -> None:
    """Revoke anonymous access, empty the bucket, and flush the cache."""
    async with connect_pg(settings.PG_DATABASE_URL) as pg_conn:
        await revoke_anonymous_access(pg_conn, settings.sync_config)

    await clear_s3_bucket()
    await clear_cache()
    logger.info("Run finalized run_id=%s", run_id)


@DBOS.workflow()
async def dump_task(task: DumpTask) -> DumpResult:
    """Dump one task, record its result, and return it to the parent workflow."""
    tablename.set(task.table)
    logger.info("Dump started task_id=%s", task.task_id)

    try:
        await extract(task)
        result: DumpResult = DumpSuccess()
    except Exception as error:
        await record_dump_failure(task, str(error))
        result = DumpFailure(failed_path=task.bucket_path)

    await record_dump_metrics(
        task.task_id, task.table, task.target_schema, result.status.value
    )
    return result


@DBOS.workflow()
async def publish_schema(
    run_id: str, plan: SyncPlan, failed_paths: set[str]
) -> set[str]:
    """Publish one schema and commit its table state."""
    schemaname.set(plan.schema_name)

    outcome = await load_and_publish(plan, failed_paths)
    await wait_replica(plan.schema_name, outcome.target_lsn)
    await refresh_schema(plan.schema_name, run_id)

    await record_publish_metrics(outcome.result, plan.schema_name)
    await commit_state(plan, outcome.result)
    return outcome.result.published_tables


@DBOS.workflow()
async def sync_run(scheduled_at: datetime, context: object) -> None:
    """Plan one run, fan out dumps, seed, fan out publishers, and finalize."""
    workflow_id = DBOS.workflow_id
    if workflow_id is None:
        raise RuntimeError("workflow_id is not set")

    with SetWorkflowTimeout(settings.SYNC_RUN_TIMEOUT_SECONDS):
        work = await build_work(workflow_id)

        if not work.plans:
            logger.info("No table changes")
            await record_run_status("no_changes")
            return

        await record_run_status("success")
        logger.info(
            "Run planned tasks=%d plans=%d",
            len(work.tasks),
            len(work.plans),
        )

        dump_handles: list[WorkflowHandleAsync[DumpResult]] = []
        for task in work.tasks:
            handle = await DBOS.enqueue_workflow_async(DUMP_QUEUE, dump_task, task)
            dump_handles.append(handle)

        dump_results: list[DumpResult] = await asyncio.gather(
            *(handle.get_result() for handle in dump_handles)
        )
        failed_paths = {
            path
            for result in dump_results
            if (path := result.maybe_failed_path) is not None
        }

        await seed(work.plans)

        publish_handles: list[WorkflowHandleAsync[set[str]]] = []
        for plan in work.plans:
            handle = await DBOS.enqueue_workflow_async(
                PUBLISH_QUEUE, publish_schema, workflow_id, plan, failed_paths
            )
            publish_handles.append(handle)

        await asyncio.gather(*(handle.get_result() for handle in publish_handles))

        await finalize(workflow_id)


def main() -> None:
    """Configure, launch, and run the DBOS sync application until stopped."""
    config: DBOSConfig = {
        "name": settings.DBOS_APPLICATION_NAME,
        "application_version": settings.DBOS_APPLICATION_VERSION,
        "system_database_url": settings.DBOS_SYSTEM_DATABASE_URL,
        "dbos_system_schema": settings.DBOS_SYSTEM_SCHEMA,
        "enable_otlp": bool(settings.OTLP_LOGS_ENDPOINT),
        "otlp_logs_endpoints": [settings.OTLP_LOGS_ENDPOINT]
        if settings.OTLP_LOGS_ENDPOINT
        else [],
    }

    DBOS(config=config)
    DBOS.listen_queues([SYNC_QUEUE, DUMP_QUEUE, PUBLISH_QUEUE])
    DBOS.launch()
    DBOS.register_queue(SYNC_QUEUE, concurrency=settings.SYNC_QUEUE_CONCURRENCY)
    DBOS.register_queue(
        DUMP_QUEUE,
        worker_concurrency=settings.DUMP_QUEUE_WORKER_CONCURRENCY,
        limiter={"limit": settings.DUMP_QUEUE_RATE_LIMIT, "period": 60},
    )
    DBOS.register_queue(
        PUBLISH_QUEUE, worker_concurrency=settings.PUBLISH_QUEUE_WORKER_CONCURRENCY
    )
    DBOS.apply_schedules(
        [
            ScheduleInput(
                schedule_name=settings.SYNC_SCHEDULE_NAME,
                workflow_fn=sync_run,
                schedule=settings.SYNC_SCHEDULE,
                queue_name=SYNC_QUEUE,
            )
        ]
    )

    logger.info("DBOS sync application started")
    threading.Event().wait()


if __name__ == "__main__":
    main()
