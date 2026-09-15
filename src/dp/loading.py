"""Synchronization plan validation and publication orchestration."""

from psycopg import AsyncConnection
from whenever import Instant

from .fallback import create_bq_views
from .freshness import record_table_failures
from .log import logger
from .models import (
    PublicationDecision,
    PublicationResult,
    SyncConfig,
    SyncPlan,
    SyncPublicationInput,
)
from .publication import prepare_tables, publish_prepared_tables, reduce_sync_plan
from .schema import initialize_schemas, reload_postgrest


def empty_incremental_tables(plan: SyncPlan) -> set[str]:
    """Return incremental tables that have no publishable data changes."""
    return {
        table
        for table, table_plan in plan.partitioned_tables.items()
        if not table_plan.full_rebuild
        and not table_plan.changed_paths
        and not table_plan.removed_partitions
    }


async def record_extraction_failures(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    source_plan: SyncPlan,
    decision: PublicationDecision,
    empty_incremental: set[str],
    attempted_at: Instant,
) -> None:
    """Record blocked tables and incremental plans with no successful task."""
    tables = {table.name: table for table in config.tables}

    failed_tables = decision.blocked_tables | empty_incremental

    partitions_by_table = {
        table_name: decision.failed_partitions.get(table_name, set())
        for table_name in empty_incremental
    }

    await record_table_failures(
        pg_conn,
        [tables[name] for name in failed_tables],
        source_plan,
        attempted_at,
        partitions_by_table,
    )


async def record_preparation_failures(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    source_plan: SyncPlan,
    eligible: set[str],
    prepared_names: set[str],
    attempted_at: Instant,
) -> None:
    """Record each eligible table that did not prepare successfully."""
    tables = {table.name: table for table in config.tables}
    failed = [tables[name] for name in eligible - prepared_names]

    if failed:
        await record_table_failures(pg_conn, failed, source_plan, attempted_at)


async def publish_eligible_tables(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    source_plan: SyncPlan,
    decision: PublicationDecision,
    eligible: set[str],
    attempted_at: Instant,
) -> set[str]:
    """Prepare eligible tables and publish each successful result."""
    prepared = await prepare_tables(pg_conn, config, decision.plan, eligible)
    logger.info("Prepared %d tables", len(prepared))

    await record_preparation_failures(
        pg_conn,
        config,
        source_plan,
        eligible,
        {prepared_table.table.name for prepared_table in prepared},
        attempted_at,
    )

    published = await publish_prepared_tables(
        pg_conn,
        prepared,
        decision.plan,
        decision.failed_partitions,
        attempted_at,
    )

    logger.info("Published %d changed tables", len(published))
    return published


def validate_and_select(
    plan: SyncPlan,
    config: SyncConfig,
    failed_paths: set[str],
) -> tuple[PublicationDecision, set[str], set[str]]:
    """Return the publication decision, the empty incremental tables, and the eligible set."""
    changed = SyncPublicationInput(config=config, plan=plan).changed_tables
    decision = reduce_sync_plan(plan, failed_paths)
    empty_incremental = empty_incremental_tables(decision.plan)
    eligible = changed - decision.blocked_tables - empty_incremental

    logger.info(
        "Validated sync plan schema=%s changed=%d eligible=%d",
        plan.schema_name,
        len(changed),
        len(eligible),
    )

    return decision, empty_incremental, eligible


async def record_publication_failures(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    source_plan: SyncPlan,
    decision: PublicationDecision,
    empty_incremental: set[str],
) -> Instant:
    """Initialize schemas and record extraction and empty-incremental failures."""
    await initialize_schemas(pg_conn, config)

    logger.info("Initialized database schemas")
    attempted_at = Instant.now()

    await record_extraction_failures(
        pg_conn, config, source_plan, decision, empty_incremental, attempted_at
    )

    return attempted_at


async def finalize_publication(pg_conn: AsyncConnection, config: SyncConfig) -> None:
    """Create fallback views and reload PostgREST after publication."""
    await create_bq_views(pg_conn, config)
    logger.info("Created BigQuery fallback views")

    await reload_postgrest(pg_conn, config)
    logger.info("PostgREST schema reload requested")


async def apply_sync_plan(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    plan: SyncPlan,
    failed_paths: set[str] | None = None,
) -> PublicationResult:
    """Apply one sync plan and return its exact published state."""
    decision, empty_incremental, eligible = validate_and_select(
        plan, config, failed_paths or set()
    )

    attempted_at = await record_publication_failures(
        pg_conn, config, plan, decision, empty_incremental
    )

    published = await publish_eligible_tables(
        pg_conn,
        config,
        plan,
        decision,
        eligible,
        attempted_at,
    )

    await finalize_publication(pg_conn, config)
    return PublicationResult(plan=decision.plan, published_tables=published)
