import asyncio
from typing import Literal

from dbos import DBOS
from lightkube import AsyncClient
from whenever import Instant

from ..cache import clear_cache
from ..constants import POOLER_SELECTOR, POSTGREST_SELECTOR
from ..duckdb import DuckDB
from ..ducklake import (
    DuckLakePaths,
    apply_maintenance,
    publish_schema,
    reader_snapshot,
)
from ..extraction import run_extraction
from ..kubernetes import (
    list_catalog_claims,
    list_deployments,
    restart_deployment,
    run_job,
)
from ..log import logger, schemaname
from ..metrics import metrics
from ..models import (
    DumpTask,
    PublicationResult,
    ServingDeployments,
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
from ..views.reconcile import reconcile_views
from .utils import retry_catalog_locked, retry_transient


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def build_sync_work(run_id: str) -> SyncWork:
    """Plan one run: detect changes and build dump tasks and schema plans."""
    logger.info("Sync planning started: workflow_id=%s", run_id)

    async with (
        DuckDB.connect() as duckdb_conn,
        Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn,
    ):
        await ensure_app_schema(pg_conn)
        work = await run_planning(
            pg_conn,
            duckdb_conn,
            settings.sync_config,
            run_id,
            settings.S3_BUCKET,
        )

    logger.info(
        "Sync planning completed: workflow_id=%s tasks=%d plans=%d",
        run_id,
        len(work.tasks),
        len(work.plans),
    )
    return work


@DBOS.step()
async def record_run_status(status: RunStatus) -> None:
    """Record one sync run status metric."""
    metrics.sync_runs_total.add(1, {"status": status})
    logger.info("Sync workflow reached terminal state: status=%s", status)


@DBOS.step()
async def record_dump_metrics(
    task_id: str, table: str, schema: str, status: str
) -> None:
    """Record dump task count metric."""
    metrics.dump_tasks_total.add(
        1, {"table": table, "schema": schema, "status": status}
    )

    logger.info("Dump completed: task_id=%s status=%s", task_id, status)


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
    """Reconcile planned serving schemas and report a PostgREST restart need."""
    logger.info("Schema seeding started: plans=%d", len(plans))

    schema_names = sorted({plan.schema_name for plan in plans})
    table_names = {
        plan.schema_name: set(plan.signatures) | set(plan.partitioned_tables)
        for plan in plans
    }

    async with Postgres.connect(settings.PG_DATABASE_URL) as pg_conn:
        postgrest_restart_required = await reconcile_views(
            pg_conn, settings.sync_config, schema_names, table_names
        )

    logger.info(
        "Schema seeding completed: plans=%d postgrest_restart_required=%s",
        len(plans),
        postgrest_restart_required,
    )
    return postgrest_restart_required


@DBOS.step()
async def record_seed_metrics(plans: list[SyncPlan]) -> None:
    """Record one successful serving-schema reconciliation per schema plan."""
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
    logger.info(
        "Extraction started: task_id=%s paths=%d", task.task_id, len(task.output_paths)
    )
    async with DuckDB.connect() as duckdb_conn:
        await run_extraction(duckdb_conn, task)
    logger.info("Extraction completed: task_id=%s", task.task_id)


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def record_dump_failure(task: DumpTask, error: str) -> None:
    """Persist one dump error in the data_proxy.errors table."""
    logger.error("Extraction failed: task_id=%s error=%s", task.task_id, error)

    async with Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        await emit_error(
            pg_conn,
            "extraction_failed",
            task=task.task_id,
            table=task.table,
            error=error,
        )


@DBOS.step(
    retries_allowed=True,
    interval_seconds=settings.DUCKLAKE_COMMIT_RETRY_SECONDS,
    backoff_rate=1.0,
    max_attempts=settings.DUCKLAKE_COMMIT_MAX_ATTEMPTS,
    should_retry=retry_catalog_locked,
)
async def commit_ducklake_snapshot(
    plan: SyncPlan, failed_paths: set[str]
) -> PublicationResult:
    """Commit scratch Parquet files into DuckLake for one schema plan."""
    schemaname.set(plan.schema_name)
    logger.info("DuckLake commit started: failed_paths=%d", len(failed_paths))

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

    logger.info(
        "DuckLake commit completed: published_tables=%d", len(result.published_tables)
    )

    return result


@DBOS.step(
    retries_allowed=True,
    interval_seconds=settings.DUCKLAKE_COMMIT_RETRY_SECONDS,
    backoff_rate=1.0,
    max_attempts=settings.DUCKLAKE_COMMIT_MAX_ATTEMPTS,
    should_retry=retry_catalog_locked,
)
async def apply_ducklake_maintenance(schema_name: str) -> int:
    """Apply DuckLake maintenance to one schema and return its snapshot."""
    schemaname.set(schema_name)
    logger.info("DuckLake maintenance started")

    encrypted = settings.sync_config.schemas[schema_name].ducklake.encrypted

    async with DuckDB.connect() as duckdb_conn:
        snapshot_id = await apply_maintenance(
            duckdb_conn, DuckLakePaths.for_schema(schema_name), encrypted
        )

    logger.info("DuckLake maintenance completed: snapshot_id=%d", snapshot_id)

    return snapshot_id


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def detect_published_schemas(
    snapshots: dict[str, int | None],
) -> dict[str, int]:
    """Return the new snapshot of every schema that published."""
    published = {
        schema: snapshot
        for schema, snapshot in sorted(snapshots.items())
        if snapshot is not None
    }
    logger.info("Published schemas detected: schemas=%d", len(published))

    return published


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def list_serving_deployments() -> ServingDeployments:
    """List the Pooler and PostgREST Deployments to restart."""
    namespace = settings.KUBERNETES_NAMESPACE

    async with AsyncClient(namespace=namespace) as client:
        poolers, postgrest = await asyncio.gather(
            list_deployments(client, namespace, POOLER_SELECTOR),
            list_deployments(client, namespace, POSTGREST_SELECTOR),
        )

    deployments = ServingDeployments(poolers=poolers, postgrest=postgrest)

    for kind, names in (
        ("PgBouncer", deployments.poolers),
        ("PostgREST", deployments.postgrest),
    ):
        if not names:
            raise RuntimeError(f"No {kind} Deployment found")

    return deployments


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def list_instance_claims() -> dict[str, str]:
    """List the catalog volume claim of every ready PostgreSQL instance."""
    namespace = settings.KUBERNETES_NAMESPACE

    async with AsyncClient(namespace=namespace) as client:
        claims = await list_catalog_claims(client, namespace)

    logger.info("Catalog claims listed: instances=%d", len(claims))

    return claims


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def run_refresh_job(schema: str, pod: str, claim: str) -> str:
    """Run the refresh Job of one schema on one instance volume."""
    namespace = settings.KUBERNETES_NAMESPACE

    async with AsyncClient(namespace=namespace) as client:
        return await run_job(client, namespace, schema, pod, claim)


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def find_lagging_snapshots(snapshots: dict[str, int]) -> dict[str, int]:
    """Return the published snapshots that the primary does not report yet."""
    lagging: dict[str, int] = {}

    async with Postgres.connect(settings.PG_DATABASE_URL) as pg_conn:
        for schema_name, snapshot_id in sorted(snapshots.items()):
            current = await reader_snapshot(pg_conn, schema_name)

            if current is None or current < snapshot_id:
                lagging[schema_name] = snapshot_id

    logger.info(
        "Reader snapshots checked: schemas=%d lagging=%d", len(snapshots), len(lagging)
    )

    return lagging


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def restart_serving(
    kind: Literal["PgBouncer", "PostgREST"], names: list[str]
) -> None:
    """Restart the Deployments together and wait for their rollouts."""
    namespace = settings.KUBERNETES_NAMESPACE
    logger.info("%s restart started: deployments=%d", kind, len(names))

    restarted_at = Instant.now().format_iso()

    async with AsyncClient(namespace=namespace) as client:
        await asyncio.gather(
            *(
                restart_deployment(
                    client,
                    namespace,
                    name,
                    restarted_at,
                    settings.DEPLOYMENT_ROLLOUT_TIMEOUT_SECONDS,
                )
                for name in names
            )
        )

    logger.info("%s restart completed: deployments=%d", kind, len(names))


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
    logger.info("State commit started: tables=%d", len(states))
    async with Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        await write_table_states(pg_conn, states)
    logger.info("State commit completed: tables=%d", len(states))


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def finalize_run(run_id: str) -> None:
    """Flush scratch Parquet files and the response cache."""
    logger.info("Sync finalization started: workflow_id=%s", run_id)

    await clear_s3_prefix(settings.S3_SCRATCH_PREFIX)

    await clear_cache()
    logger.info("Sync finalization completed: workflow_id=%s", run_id)
