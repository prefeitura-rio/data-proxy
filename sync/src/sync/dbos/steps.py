"""Durable DBOS steps used by the synchronization workflows."""

from typing import TYPE_CHECKING

from dbos import DBOS
from httpx2 import AsyncClient as HttpClient

from sync.cache import clear_cache
from sync.duckdb import DuckDB
from sync.ducklake import (
    DuckLakePaths,
    apply_maintenance,
    attach_catalog,
    drop_tables_except,
    publish_schema,
    table_prefix,
)
from sync.extraction import run_extraction
from sync.identities import remove_stale_identities
from sync.kubernetes import create_job, k8s_client, wait_for_job
from sync.log import SCHEMA, TABLE, summarize_exception
from sync.metrics import metrics
from sync.models import (
    DumpStatus,
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
    ScopeRow,
    delete_removed_state,
    emit_error,
    read_sync_config,
    write_scope,
)
from sync.utils import sha256_hex
from sync.views.reconcile import reconcile_views

from . import logs
from .utils import retry_catalog_locked, retry_transient

if TYPE_CHECKING:
    from collections.abc import Callable

    from sync.types import RunStatus


def transient_step[**P, R](step: Callable[P, R]) -> Callable[P, R]:
    """Make a durable step that retries a backend failure, but not a bug."""
    return DBOS.step(
        retries_allowed=True,
        max_attempts=settings.SYNC_STEP_MAX_ATTEMPTS,
        should_retry=retry_transient,
    )(step)


def catalog_step[**P, R](step: Callable[P, R]) -> Callable[P, R]:
    """Make a durable step that waits and retries while another process holds the DuckLake catalog lock."""
    return DBOS.step(
        retries_allowed=True,
        interval_seconds=settings.DUCKLAKE_COMMIT_RETRY_SECONDS,
        backoff_rate=1.0,
        max_attempts=settings.DUCKLAKE_COMMIT_MAX_ATTEMPTS,
        should_retry=retry_catalog_locked,
    )(step)


def schema_config(document: str, schema: str) -> SyncConfig:
    """Return the config of a run, reduced to one schema."""
    schemas = SyncConfig.model_validate_json(document).schemas
    return SyncConfig(schemas={schema: schemas[schema]})


@transient_step
async def load_sync_config() -> LoadedConfig:
    """Load the sync.json that one run uses, from the admin tables.

    The run keeps this document from start to end, so a change never reaches a run
    that already started.
    """
    async with Postgres.connect(
        settings.DBOS_SYSTEM_DATABASE_URL.get_secret_value()
    ) as pg_conn:
        document = await read_sync_config(pg_conn)

    loaded = LoadedConfig(revision=sha256_hex(document)[:12], document=document)

    config = SyncConfig.model_validate_json(loaded.document)
    await logs.info(
        "Sync config loaded", revision=loaded.revision, schemas=len(config.schemas)
    )
    return loaded


@transient_step
async def build_sync_work(run_id: str, document: str, scope: SyncScope) -> SyncWork:
    """Plan one run: detect changes and build dump tasks and schema plans."""
    await logs.info("Sync planning started")

    async with (
        DuckDB.connect() as duckdb_conn,
        Postgres.connect(
            settings.DBOS_SYSTEM_DATABASE_URL.get_secret_value()
        ) as pg_conn,
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


@transient_step
async def seed_schemas(plans: list[SyncPlan], document: str) -> bool:
    """Reconcile planned serving schemas and report a PostgREST reload need.

    The reconcile covers every table of a planned schema, and not only the tables of the plans.
    A release can change the generated definitions of a table whose data did not change, and the stored
    signature skips the tables that did not.
    """
    await logs.info("Schema seeding started", plans=len(plans))

    config = SyncConfig.model_validate_json(document)
    schema_names = sorted({plan.schema_name for plan in plans})
    table_names = {
        name: {table.name for table in config.schemas[name].tables}
        for name in schema_names
    }

    async with Postgres.connect(settings.PG_DATABASE_URL.get_secret_value()) as pg_conn:
        postgrest_reload_required = await reconcile_views(
            pg_conn, config, schema_names, table_names
        )

    for plan in plans:
        metrics.seed_runs_total.add(
            1, {"schema": plan.schema_name, "status": "success"}
        )

    await logs.info(
        "Schema seeding completed",
        plans=len(plans),
        postgrest_reload_required=postgrest_reload_required,
    )
    return postgrest_reload_required


@transient_step
async def reconcile_idle_schemas(document: str, scope: SyncScope) -> bool:
    """Reconcile the serving views of a run that planned no change, and report a PostgREST reload need.

    A new release can change the generated definitions without a change in the data.
    The stored signatures tell which table needs a rebuild. The reconcile gets the full config,
    so its cleanup never drops a table outside the scope of the run.
    """
    config = SyncConfig.model_validate_json(document)
    scoped = config.scoped(scope)
    table_names = {
        name: {table.name for table in schema.tables}
        for name, schema in scoped.schemas.items()
    }

    await logs.info("Idle reconcile started", schemas=len(table_names))

    async with Postgres.connect(settings.PG_DATABASE_URL.get_secret_value()) as pg_conn:
        postgrest_reload_required = await reconcile_views(
            pg_conn, config, sorted(table_names), table_names
        )

    await logs.info(
        "Idle reconcile completed", postgrest_reload_required=postgrest_reload_required
    )
    return postgrest_reload_required


@transient_step
async def record_scope(
    run_id: str, schemas: list[str], dumps: list[tuple[str, str, str]]
) -> None:
    """Record the schemas of the run and of each dump task, so the console can scope them."""
    rows = [ScopeRow(run_id, run_id, name, "") for name in schemas]
    rows += [ScopeRow(task, run_id, name, table) for task, name, table in dumps]

    async with Postgres.connect(settings.PG_DATABASE_URL.get_secret_value()) as pg_conn:
        await write_scope(pg_conn, rows)


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

    async def report(message: str, /, **fields: str | float | bool) -> None:
        await logs.info(message, task_id=task.task_id, **fields)

    try:
        async with DuckDB.connect() as duckdb_conn:
            await run_extraction(duckdb_conn, task, report)
    except Exception as error:
        await logs.warning(
            "Extraction attempt failed",
            task_id=task.task_id,
            error=summarize_exception(error),
        )
        raise

    metrics.dump_tasks_total.add(
        1,
        {
            "table": task.table,
            "schema": task.target_schema,
            "status": DumpStatus.SUCCESS.value,
        },
    )
    await logs.info("Extraction completed", task_id=task.task_id)


@transient_step
async def record_dump_failure(task: DumpTask, error: str) -> None:
    """Persist one dump error in the data_proxy.errors table."""
    metrics.dump_tasks_total.add(
        1,
        {
            "table": task.table,
            "schema": task.target_schema,
            "status": DumpStatus.FAILURE.value,
        },
    )
    await logs.error("Extraction failed", task_id=task.task_id, error=error)

    async with Postgres.connect(
        settings.DBOS_SYSTEM_DATABASE_URL.get_secret_value()
    ) as pg_conn:
        await emit_error(
            pg_conn,
            "extraction_failed",
            task=task.task_id,
            table=task.table,
            error=error,
        )


@catalog_step
async def commit_ducklake_snapshot(
    plan: SyncPlan, failed_paths: set[str], document: str
) -> PublicationResult:
    """Commit scratch Parquet files into DuckLake for one schema plan."""
    SCHEMA.set(plan.schema_name)
    await logs.info("DuckLake commit started", failed_paths=len(failed_paths))

    config = schema_config(document, plan.schema_name)

    async with (
        DuckDB.connect() as duckdb_conn,
        Postgres.connect(
            settings.DBOS_SYSTEM_DATABASE_URL.get_secret_value()
        ) as pg_conn,
    ):
        result = await publish_schema(
            duckdb_conn,
            pg_conn,
            config,
            plan,
            failed_paths,
        )

    published = len(result.published_tables)
    failed = len(plan.signatures) - published

    for status, count in (("success", published), ("error", failed)):
        if count != 0:
            metrics.publish_tables_total.add(
                count, {"schema": plan.schema_name, "status": status}
            )

    for table in sorted(result.published_tables):
        token = TABLE.set(table)
        await logs.info("Table published")
        TABLE.reset(token)

    snapshot = {} if result.snapshot_id is None else {"snapshot_id": result.snapshot_id}
    await logs.info(
        "DuckLake commit completed",
        published_tables=len(result.published_tables),
        failed_paths=len(failed_paths),
        **snapshot,
    )

    return result


@catalog_step
async def apply_ducklake_maintenance(schema_name: str, document: str) -> int:
    """Apply DuckLake maintenance to one schema and return its snapshot."""
    SCHEMA.set(schema_name)
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


@catalog_step
async def remove_dropped_tables(document: str) -> list[str]:
    """Remove the tables that the console no longer lists: the catalog entry, the data files and the state.

    A run that stops after the drop leaves its files behind. The orphan cleanup of the maintenance deletes them later.
    """
    config = SyncConfig.model_validate_json(document)
    removed: list[str] = []

    for schema_name, schema_config_ in config.schemas.items():
        SCHEMA.set(schema_name)
        async with DuckDB.connect() as duckdb_conn:
            await attach_catalog(
                duckdb_conn,
                DuckLakePaths.for_schema(schema_name),
                schema_config_.ducklake.encrypted,
            )
            dropped = await drop_tables_except(
                duckdb_conn, {table.table_name for table in schema_config_.tables}
            )

        for table in dropped:
            await clear_s3_prefix(table_prefix(schema_name, table))
            removed.append(f"{schema_name}.{table}")

    async with Postgres.connect(
        settings.DBOS_SYSTEM_DATABASE_URL.get_secret_value()
    ) as pg_conn:
        await delete_removed_state(pg_conn, {table.name for table in config.tables})

    await logs.info("Removed tables", tables=",".join(removed))

    return removed


@transient_step
async def cleanup_s3_credentials(document: str) -> list[str]:
    """Delete the S3 identities that the console made for schemas that the config no longer lists."""
    if not settings.S3_IDENTITIES:
        await logs.warning(
            "S3 credential cleanup skipped", reason="S3_IDENTITIES is not set"
        )
        return []

    removed = await remove_stale_identities(
        set(SyncConfig.model_validate_json(document).schemas)
    )
    await logs.info("Removed S3 credentials", schemas=",".join(removed))

    return removed


@transient_step
async def reload_postgrest() -> None:
    """Tell PostgREST to read the schema again."""
    async with Postgres.connect(settings.PG_DATABASE_URL.get_secret_value()) as pg_conn:
        await notify_postgrest(pg_conn)

    await logs.info("PostgREST reload requested")


@transient_step
async def finalize_run() -> None:
    """Flush scratch Parquet files and the response cache."""
    await logs.info("Sync finalization started")

    await clear_s3_prefix(settings.S3_SCRATCH_PREFIX)

    await clear_cache()
    await logs.info("Sync finalization completed")


@transient_step
async def run_provision() -> None:
    """Make a Job from the provision CronJob and wait for it."""
    cronjob = settings.PROVISION_CRONJOB

    if cronjob is None:
        await logs.warning("Provision skipped", reason="PROVISION_CRONJOB is not set")
        return

    async with k8s_client() as client:
        job = await create_job(client, cronjob)
        await logs.info("Provision started", job=job)
        await wait_for_job(client, job, settings.PROVISION_TIMEOUT_SECONDS)

    await logs.info("Provision completed", job=job)


@transient_step
async def notify_proxies() -> None:
    """Ask every proxy pod to read the configuration again."""
    url = settings.PROXY_ADMIN_URL

    if url is None:
        await logs.info("Proxy reload skipped", reason="PROXY_ADMIN_URL is not set")
        return

    async with Postgres.connect(settings.PG_DATABASE_URL.get_secret_value()) as pg_conn:
        await wait_for_standbys(pg_conn)

    addresses = await proxy_addresses(url)

    async with HttpClient(timeout=5) as client:
        await reload_proxies(client, url, addresses)

    await logs.info("Proxy reload completed", pods=len(addresses))
