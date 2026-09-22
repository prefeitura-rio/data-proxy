from asyncio import gather

from dbos import DBOS

from ..cache import clear_cache
from ..duckdb import DuckDB
from ..extraction import run_extraction
from ..kubernetes import (
    api_client_factory,
    apps_factory,
    deployment_ready,
    expand_template,
    load_config,
)
from ..log import logger, schemaname
from ..metrics import RunStatus, metrics
from ..models import (
    DumpTask,
    PublicationResult,
    PublishedSchema,
    SyncConfig,
    SyncPlan,
    SyncWork,
)
from ..planning import run_planning
from ..postgres import connect_pg
from ..publication import configure_s3_secret, run_publication
from ..replication import current_wal_lsn, replicas_replayed
from ..s3 import clear_s3_bucket
from ..schema import initialize_schemas
from ..settings import settings
from ..state import (
    build_table_states,
    emit_error,
    ensure_app_schema,
    write_table_states,
)
from ..utils import wait_for
from .utils import group_schema_configs_by_dsn, retry_transient


@DBOS.step()
async def build_sync_work(run_id: str) -> SyncWork:
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
async def record_run_status(status: RunStatus) -> None:
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


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def seed_schemas(plans: list[SyncPlan]) -> None:
    """Initialize configured schemas and policy objects for one run."""
    by_dsn = group_schema_configs_by_dsn(
        plans, settings.sync_config, settings.SCHEMA_WRITERS
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
    should_retry=retry_transient,
)
async def extract_task(task: DumpTask) -> None:
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
        connect_pg(settings.DBOS_SYSTEM_DATABASE_URL) as dbos_conn,
    ):
        await configure_s3_secret(pg_conn)

        result = await run_publication(pg_conn, dbos_conn, config, plan, failed_paths)
        target_lsn = await current_wal_lsn(pg_conn)

    return PublishedSchema(result=result, target_lsn=target_lsn)


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def wait_for_replica(schema_name: str, target_lsn: str) -> None:
    """Wait until every replica has replayed the publication WAL position."""
    async with connect_pg(settings.SCHEMA_WRITERS.dsn(schema_name)) as pg_conn:
        await wait_for(
            lambda: replicas_replayed(pg_conn, target_lsn),
            timeout=settings.REPLICATION_WAIT_TIMEOUT_SECONDS,
            interval=settings.REPLICATION_POLL_INTERVAL_SECONDS,
            message="PostgreSQL standbys did not replay the publication WAL",
        )


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def restart_postgrest(schema_name: str, run_id: str) -> None:
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


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def commit_table_state(plan: SyncPlan, result: PublicationResult) -> None:
    """Persist committed table state for one published schema."""
    config = SyncConfig(
        schemas={plan.schema_name: settings.sync_config.schemas[plan.schema_name]}
    )
    states = build_table_states(result, config)
    async with connect_pg(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        await write_table_states(pg_conn, states)


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def finalize_run(run_id: str) -> None:
    """Empty the temporary object store and flush the response cache."""
    await clear_s3_bucket()
    await clear_cache()
    logger.info("Run finalized run_id=%s", run_id)
