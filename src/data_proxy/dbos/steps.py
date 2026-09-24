from dbos import DBOS
from whenever import Instant

from ..cache import clear_cache
from ..duckdb import DuckDB
from ..ducklake import expire_ducklake_snapshots, run_ducklake_publication
from ..extraction import run_extraction
from ..fallback import run_fallback_views_creation
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
    SyncConfig,
    SyncPlan,
    SyncWork,
)
from ..planning import run_planning
from ..postgres import connect_pg
from ..s3 import clear_s3_prefix
from ..settings import settings
from ..state import (
    build_table_states,
    emit_error,
    ensure_app_schema,
    write_table_states,
)
from ..utils import wait_for
from .utils import retry_transient


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
    """Record publication success and error metrics."""
    success_count = len(result.published_tables)
    if success_count:
        metrics.publish_tables_total.add(
            success_count, {"schema": schema_name, "status": "success"}
        )

    failure_count = len(result.plan.signatures) - success_count
    if failure_count:
        metrics.publish_tables_total.add(
            failure_count, {"schema": schema_name, "status": "error"}
        )


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def seed_schemas(plans: list[SyncPlan]) -> bool:
    """Ensure configured PostgreSQL views exist and report view-set changes.

    The one-time database setup creates roles and metadata tables. This step
    only reconciles default DuckLake views and optional BigQuery fallback views.
    """
    schema_changed = False

    async with connect_pg(settings.PG_DATABASE_URL) as pg_conn:
        views_changed = await run_fallback_views_creation(pg_conn, settings.sync_config)
        schema_changed = schema_changed or views_changed

    for plan in plans:
        metrics.seed_runs_total.add(
            1, {"schema": plan.schema_name, "status": "success"}
        )

    return schema_changed


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
    """Persist one dump error in the data_proxy.errors table."""
    async with connect_pg(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        await emit_error(
            pg_conn,
            "extraction_failed",
            task=task.task_id,
            table=task.table,
            error=error,
        )


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def expire_ducklake_catalogs(schema: str) -> None:
    """Expire snapshots in one local publisher catalog."""
    await expire_ducklake_snapshots({schema})


@DBOS.step()
async def commit_ducklake_snapshot(
    plan: SyncPlan, failed_paths: set[str]
) -> PublicationResult:
    """Commit scratch Parquet files into DuckLake for one schema plan."""
    schemaname.set(plan.schema_name)
    config = SyncConfig(
        schemas={plan.schema_name: settings.sync_config.schemas[plan.schema_name]}
    )

    async with (
        connect_pg(settings.PG_DATABASE_URL) as pg_conn,
        connect_pg(settings.DBOS_SYSTEM_DATABASE_URL) as dbos_conn,
    ):
        result = await run_ducklake_publication(
            pg_conn, dbos_conn, config, plan, failed_paths
        )

    return result


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def restart_postgrest(schema_name: str, run_id: str) -> None:
    """Restart the PostgREST deployment and wait for its rollout."""
    load_config()
    logger.info("Restarting PostgREST schema=%s run_id=%s", schema_name, run_id)

    async with api_client_factory() as api_client:
        apps = apps_factory(api_client)
        namespace = settings.KUBERNETES_NAMESPACE
        name = expand_template(settings.POSTGREST_DEPLOYMENT_TEMPLATE, schema_name)
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": Instant.now().format_iso(),
                        }
                    }
                }
            }
        }

        await apps.patch_namespaced_deployment(
            name=name,
            namespace=namespace,
            body=patch,
        )

        await wait_for(
            lambda: deployment_ready(
                lambda: apps.read_namespaced_deployment(name=name, namespace=namespace)
            ),
            timeout=settings.POSTGREST_ROLLOUT_TIMEOUT_SECONDS,
            interval=2,
            message=f"PostgREST rollout did not become ready: {name}",
        )


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
    """Flush scratch Parquet files and the response cache."""
    await clear_s3_prefix(settings.S3_SCRATCH_PREFIX)
    await clear_cache()
    logger.info("Run finalized run_id=%s", run_id)
