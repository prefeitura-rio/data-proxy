"""Durable DBOS steps used by the synchronization workflows."""

from typing import TYPE_CHECKING

from dbos import DBOS
from httpx2 import AsyncClient as HttpClient

from sync.cache import clear_cache
from sync.duckdb import DuckDB
from sync.ducklake import (
    DuckLakePaths,
    apply_maintenance,
    publish_schema,
)
from sync.extraction import run_extraction
from sync.log import schemaname
from sync.metrics import metrics
from sync.models import (
    DumpTask,
    LoadedConfig,
    PublicationResult,
    SyncConfig,
    SyncPlan,
    SyncScope,
    SyncWork,
)
from sync.planning import run_planning
from sync.postgres import Postgres
from sync.proxies import proxy_addresses, reload_proxies
from sync.s3 import clear_s3_prefix
from sync.schema import notify_postgrest, wait_for_standbys
from sync.settings import settings
from sync.state import (
    build_table_states,
    emit_error,
    read_sync_config,
    write_table_states,
)
from sync.utils import sha256_hex
from sync.views.reconcile import reconcile_views

from . import logs
from .utils import retry_catalog_locked, retry_transient

if TYPE_CHECKING:
    from sync.types import RunStatus


def schema_config(document: str, schema: str) -> SyncConfig:
    """Return the config of a run, reduced to one schema."""
    schemas = SyncConfig.model_validate_json(document).schemas
    return SyncConfig(schemas={schema: schemas[schema]})


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def load_sync_config() -> LoadedConfig:
    """Load the sync.json that one run uses, from the admin tables.

    The run keeps this document from start to end, so a change never reaches a run
    that already started.
    """
    async with Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        document = await read_sync_config(pg_conn)

    loaded = LoadedConfig(revision=sha256_hex(document)[:12], document=document)

    config = SyncConfig.model_validate_json(loaded.document)
    await logs.info(
        "Sync config loaded", revision=loaded.revision, schemas=len(config.schemas)
    )
    return loaded


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def build_sync_work(run_id: str, document: str, scope: SyncScope) -> SyncWork:
    """Plan one run: detect changes and build dump tasks and schema plans."""
    await logs.info("Sync planning started")

    async with (
        DuckDB.connect() as duckdb_conn,
        Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn,
    ):
        work = await run_planning(
            pg_conn,
            duckdb_conn,
            SyncConfig.model_validate_json(document).scoped(scope),
            run_id,
            settings.S3_BUCKET,
        )

    await logs.info(
        "Sync planning completed", tasks=len(work.tasks), plans=len(work.plans)
    )
    return work


@DBOS.step()
async def record_run_status(status: RunStatus) -> None:
    """Record one sync run status metric."""
    metrics.sync_runs_total.add(1, {"status": status})
    await logs.info("Sync workflow reached terminal state", status=status)


@DBOS.step()
async def record_dump_metrics(
    task_id: str, table: str, schema: str, status: str
) -> None:
    """Record dump task count metric."""
    metrics.dump_tasks_total.add(
        1, {"table": table, "schema": schema, "status": status}
    )

    await logs.info("Dump completed", task_id=task_id, status=status)


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
async def seed_schemas(plans: list[SyncPlan], document: str) -> bool:
    """Reconcile planned serving schemas and report a PostgREST reload need."""
    await logs.info("Schema seeding started", plans=len(plans))

    schema_names = sorted({plan.schema_name for plan in plans})
    table_names = {
        plan.schema_name: set(plan.signatures) | set(plan.partitioned_tables)
        for plan in plans
    }

    async with Postgres.connect(settings.PG_DATABASE_URL) as pg_conn:
        postgrest_reload_required = await reconcile_views(
            pg_conn, SyncConfig.model_validate_json(document), schema_names, table_names
        )

    await logs.info(
        "Schema seeding completed",
        plans=len(plans),
        postgrest_reload_required=postgrest_reload_required,
    )
    return postgrest_reload_required


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
    """Extract one dump task from its source to Parquet."""
    await logs.info(
        "Extraction started", task_id=task.task_id, paths=len(task.output_paths)
    )
    async with DuckDB.connect() as duckdb_conn:
        await run_extraction(duckdb_conn, task)
    await logs.info("Extraction completed", task_id=task.task_id)


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def record_dump_failure(task: DumpTask, error: str) -> None:
    """Persist one dump error in the data_proxy.errors table."""
    await logs.error("Extraction failed", task_id=task.task_id, error=error)

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
    plan: SyncPlan, failed_paths: set[str], document: str
) -> PublicationResult:
    """Commit scratch Parquet files into DuckLake for one schema plan."""
    schemaname.set(plan.schema_name)
    await logs.info("DuckLake commit started", failed_paths=len(failed_paths))

    config = schema_config(document, plan.schema_name)

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

    await logs.info(
        "DuckLake commit completed", published_tables=len(result.published_tables)
    )

    return result


@DBOS.step(
    retries_allowed=True,
    interval_seconds=settings.DUCKLAKE_COMMIT_RETRY_SECONDS,
    backoff_rate=1.0,
    max_attempts=settings.DUCKLAKE_COMMIT_MAX_ATTEMPTS,
    should_retry=retry_catalog_locked,
)
async def apply_ducklake_maintenance(schema_name: str, document: str) -> int:
    """Apply DuckLake maintenance to one schema and return its snapshot."""
    schemaname.set(schema_name)
    await logs.info("DuckLake maintenance started")

    async with DuckDB.connect() as duckdb_conn:
        snapshot_id = await apply_maintenance(
            duckdb_conn,
            DuckLakePaths.for_schema(schema_name),
            schema_config(document, schema_name)
            .schemas[schema_name]
            .ducklake.encrypted,
        )

    await logs.info("DuckLake maintenance completed", snapshot_id=snapshot_id)

    return snapshot_id


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def reload_postgrest() -> None:
    """Tell PostgREST to read the schema again."""
    async with Postgres.connect(settings.PG_DATABASE_URL) as pg_conn:
        await notify_postgrest(pg_conn)

    await logs.info("PostgREST reload requested")


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def commit_table_state(
    plan: SyncPlan, result: PublicationResult, document: str
) -> None:
    """Persist committed table state for one published schema."""
    config = schema_config(document, plan.schema_name)
    states = build_table_states(result, config)
    await logs.info("State commit started", tables=len(states))
    async with Postgres.connect(settings.DBOS_SYSTEM_DATABASE_URL) as pg_conn:
        await write_table_states(pg_conn, states)
    await logs.info("State commit completed", tables=len(states))


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def finalize_run() -> None:
    """Flush scratch Parquet files and the response cache."""
    await logs.info("Sync finalization started")

    await clear_s3_prefix(settings.S3_SCRATCH_PREFIX)

    await clear_cache()
    await logs.info("Sync finalization completed")


@DBOS.step(
    retries_allowed=True,
    max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
    should_retry=retry_transient,
)
async def notify_proxies() -> None:
    """Ask every proxy pod to read the configuration again."""
    url = settings.PROXY_ADMIN_URL

    if url is None:
        await logs.info("Proxy reload skipped", reason="PROXY_ADMIN_URL is not set")
        return

    async with Postgres.connect(settings.PG_DATABASE_URL) as pg_conn:
        await wait_for_standbys(pg_conn)

    addresses = await proxy_addresses(url)

    async with HttpClient(timeout=5) as client:
        await reload_proxies(client, url, addresses)

    await logs.info("Proxy reload completed", pods=len(addresses))
