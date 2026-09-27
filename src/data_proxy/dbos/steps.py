from dbos import DBOS
from whenever import Instant

from ..cache import clear_cache
from ..duckdb import DuckDB
from ..ducklake import publish_schema
from ..extraction import run_extraction
from ..fallback import reconcile_views
from ..kubernetes import restart_postgrest as restart_postgrest_deployment
from ..log import logger, schemaname
from ..metrics import metrics
from ..models import (
    DumpTask,
    PublicationResult,
    SyncConfig,
    SyncPlan,
    SyncWork,
)
from ..planning import run_planning
from ..postgres import Postgres
from ..s3 import clear_s3_prefix
from ..settings import settings
from ..state import (
    build_table_states,
    emit_error,
    ensure_app_schema,
    write_table_states,
)
from ..types import RunStatus
from .utils import retry_transient


@DBOS.step()
async def build_sync_work(run_id: str) -> SyncWork:
    """Plan one run: detect changes and build dump tasks and schema plans."""
    async with (
        DuckDB.connect() as duckdb_conn,
        Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn,
    ):
        await ensure_app_schema(pg_conn)
        return await run_planning(
            pg_conn,
            duckdb_conn,
            settings.sync_config,
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

    async with Postgres.connect(settings.PG_DATABASE_URL) as pg_conn:
        views_changed = await reconcile_views(pg_conn, settings.sync_config)
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
        await run_extraction(duckdb_conn, task)


@DBOS.step()
async def record_dump_failure(task: DumpTask, error: str) -> None:
    """Persist one dump error in the data_proxy.errors table."""
    async with Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        await emit_error(
            pg_conn,
            "extraction_failed",
            task=task.task_id,
            table=task.table,
            error=error,
        )


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
        DuckDB.connect() as duckdb_conn,
        Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn,
    ):
        result = await publish_schema(
            duckdb_conn,
            pg_conn,
            config,
            plan,
            failed_paths,
        )

    return result


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def restart_postgrest(schema_name: str, run_id: str) -> None:
    """Restart the PostgREST deployment and wait for its rollout."""
    logger.info("Restarting PostgREST schema=%s run_id=%s", schema_name, run_id)
    await restart_postgrest_deployment(
        namespace=settings.KUBERNETES_NAMESPACE,
        restarted_at=Instant.now().format_iso(),
        timeout=settings.POSTGREST_ROLLOUT_TIMEOUT_SECONDS,
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
    async with Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
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
