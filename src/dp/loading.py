"""Synchronization plan validation and publication orchestration."""

from duckdb import DuckDBPyConnection
from psycopg import Connection
from whenever import Instant

from dp.log import logger

from .freshness import record_table_failures
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

    failed_tables = decision.blocked_tables | empty_incremental
    partitions_by_table = {
        table_name: decision.failed_partitions.get(table_name, set())
        for table_name in empty_incremental
    }
    record_table_failures(
        pg_conn,
        [tables[table_name] for table_name in failed_tables],
        source_plan,
        attempted_at,
        partitions_by_table,
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
    failed = [tables[table_name] for table_name in eligible - prepared_names]

    if failed:
        record_table_failures(pg_conn, failed, source_plan, attempted_at)


def publish_eligible_tables(
    pg_conn: Connection,
    duckdb_conn: DuckDBPyConnection,
    config: SyncConfig,
    source_plan: SyncPlan,
    decision: PublicationDecision,
    eligible: set[str],
    attempted_at: Instant,
) -> set[str]:
    """Prepare eligible tables and publish each successful result."""
    prepared = prepare_tables(pg_conn, duckdb_conn, config, decision.plan, eligible)
    logger.info("Prepared %d tables", len(prepared))

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

    logger.info("Published %d changed tables", len(published))
    return published


def apply_sync_plan(
    pg_conn: Connection,
    duckdb_conn: DuckDBPyConnection,
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
        "Validated sync plan changed=%d eligible=%d", len(changed), len(eligible)
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
    logger.info("PostgREST schema reload requested")
    return PublicationResult(plan=publication_plan, published_tables=published)
