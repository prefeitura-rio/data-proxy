"""Synchronization plan validation and publication orchestration."""

from loguru import logger
from psycopg import Connection
from whenever import Instant

from .duckdb import DBConnection
from .freshness import record_table_failure, upsert_freshness
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


def record_extraction_failures(
    pg_conn: Connection,
    config: SyncConfig,
    source_plan: SyncPlan,
    decision: PublicationDecision,
    empty_incremental: set[str],
    attempted_at: Instant,
) -> None:
    """Record blocked tables and incremental plans with no successful task."""
    tables = {table.name: table for table in config.tables}
    for table_name in decision.blocked_tables:
        record_table_failure(pg_conn, tables[table_name], source_plan, attempted_at)
    for table_name in empty_incremental:
        table = tables[table_name]
        with pg_conn.transaction():
            for partition_id in decision.failed_partitions.get(table_name, set()):
                upsert_freshness(
                    pg_conn,
                    table,
                    partition_id,
                    attempted_at,
                    success=False,
                )


def record_preparation_failures(
    pg_conn: Connection,
    config: SyncConfig,
    source_plan: SyncPlan,
    eligible: set[str],
    prepared_names: set[str],
    attempted_at: Instant,
) -> None:
    """Record each eligible table that did not prepare successfully."""
    tables = {table.name: table for table in config.tables}
    for table_name in eligible - prepared_names:
        record_table_failure(pg_conn, tables[table_name], source_plan, attempted_at)


def publish_eligible_tables(
    pg_conn: Connection,
    duckdb_conn: DBConnection,
    config: SyncConfig,
    source_plan: SyncPlan,
    decision: PublicationDecision,
    eligible: set[str],
    attempted_at: Instant,
) -> set[str]:
    """Prepare eligible tables and publish each successful result."""
    prepared = prepare_tables(pg_conn, duckdb_conn, config, decision.plan, eligible)
    logger.info("Prepared {} tables", len(prepared))

    record_preparation_failures(
        pg_conn,
        config,
        source_plan,
        eligible,
        {table.name for table in prepared},
        attempted_at,
    )

    published = publish_prepared_tables(
        pg_conn,
        prepared,
        decision.plan,
        decision.failed_partitions,
        attempted_at,
    )

    logger.info("Published {} changed tables", len(published))
    return published


def apply_sync_plan(
    pg_conn: Connection,
    duckdb_conn: DBConnection,
    config: SyncConfig,
    plan: SyncPlan,
    failed_paths: set[str] | None = None,
) -> PublicationResult:
    """Apply one sync plan and return its exact published state."""
    publication_input = SyncPublicationInput(config=config, plan=plan)
    changed = publication_input.changed_tables
    decision = reduce_sync_plan(plan, failed_paths or set())
    publication_plan = decision.plan
    empty_incremental = empty_incremental_tables(publication_plan)
    eligible = changed - decision.blocked_tables - empty_incremental

    logger.info(
        "Validated sync plan with {} changed and {} eligible tables",
        len(changed),
        len(eligible),
    )

    initialize_schemas(pg_conn, config)
    logger.info("Initialized database schemas")
    attempted_at = Instant.now()
    record_extraction_failures(
        pg_conn, config, plan, decision, empty_incremental, attempted_at
    )

    published = publish_eligible_tables(
        pg_conn,
        duckdb_conn,
        config,
        plan,
        decision,
        eligible,
        attempted_at,
    )

    reload_postgrest(pg_conn, config)
    logger.info("Requested PostgREST schema reload")
    return PublicationResult(plan=publication_plan, published_tables=published)
