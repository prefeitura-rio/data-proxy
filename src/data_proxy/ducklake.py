"""Publish scratch Parquet files into per-schema DuckLake catalogs."""

from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from psycopg import AsyncConnection
from whenever import Instant

from .catalog import CatalogPaths
from .conditions import partition_condition
from .duckdb import DuckDB
from .freshness import update_published_freshness
from .models import (
    PartitionedTablePlan,
    PhysicalPartition,
    PublicationDecision,
    PublicationResult,
    SyncConfig,
    SyncPlan,
    SyncPublicationInput,
    TableConfig,
)
from .settings import settings
from .state import emit_error


@dataclass(frozen=True, slots=True)
class DuckLakePaths:
    """Local catalog and S3 data locations for one schema."""

    catalog: Path
    data: str

    @classmethod
    def for_schema(cls, schema: str) -> DuckLakePaths:
        """Build locations for one schema."""
        catalog = CatalogPaths.for_schema(schema).local
        return cls(
            catalog=catalog,
            data=f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        )


def planned_paths(
    plan: SyncPlan, table: str, partitioned: PartitionedTablePlan | None
) -> list[str]:
    """Return scratch paths for one table."""
    match partitioned:
        case PartitionedTablePlan():
            return list(dict.fromkeys(partitioned.changed_paths.values()))
        case None:
            return plan.paths.get(table, [])
        case _:
            assert_never(partitioned)


def empty_incremental_tables(plan: SyncPlan) -> set[str]:
    """Return incremental tables with no data changes."""
    return {
        name
        for name, table_plan in plan.partitioned_tables.items()
        if not table_plan.full_rebuild
        and not table_plan.changed_paths
        and not table_plan.removed_partitions
    }


def reduce_sync_plan(plan: SyncPlan, failed_paths: set[str]) -> PublicationDecision:
    """Remove failed extraction outputs from a publication plan."""
    reduced = plan.model_copy(deep=True)
    blocked = {
        table for table, paths in plan.paths.items() if failed_paths.intersection(paths)
    }
    failed_partitions: dict[str, set[str]] = {}

    for table, table_plan in reduced.partitioned_tables.items():
        failed_ids = {
            partition_id
            for partition_id, path in table_plan.changed_paths.items()
            if path in failed_paths
        }
        if not failed_ids:
            continue
        failed_partitions[table] = failed_ids
        if table_plan.full_rebuild:
            blocked.add(table)
        else:
            for partition_id in failed_ids:
                table_plan.changed_paths.pop(partition_id, None)
                previous = table_plan.previous_partitions.get(partition_id)
                if previous is None:
                    table_plan.current_partitions.pop(partition_id, None)
                else:
                    table_plan.current_partitions[partition_id] = previous

    return PublicationDecision(
        plan=reduced, blocked_tables=blocked, failed_partitions=failed_partitions
    )


def identifier(value: str) -> str:
    """Quote a DuckDB identifier."""
    return '"' + value.replace('"', '""') + '"'


async def ensure_table(
    duckdb: DuckDB,
    table: TableConfig,
    path: str,
    partitioned: PartitionedTablePlan | None,
) -> None:
    """Create a missing DuckLake table from the first scratch file."""
    name = identifier(table.table_name)
    exists = await duckdb.fetchall(
        "SELECT count(*) FROM dl.ducklake_table WHERE table_name = ?",
        [table.table_name],
    )
    if exists and int(str(exists[0][0])) > 0:
        return

    await duckdb.execute(
        f"CREATE TABLE IF NOT EXISTS dl.{name} AS SELECT * FROM read_parquet(?) LIMIT 0",
        [path],
    )

    columns = [column for index in table.indexes for column in (index.columns or [])]
    if columns:
        order = ", ".join(identifier(column) for column in dict.fromkeys(columns))
        await duckdb.execute(f"ALTER TABLE dl.{name} SET SORTED BY ({order})")

    if partitioned is not None and partitioned.current_partitions:
        first = next(iter(partitioned.current_partitions.values()))
        selection = first.selection
        column = getattr(selection, "column", None)
        if isinstance(column, str):
            await duckdb.execute(
                f"ALTER TABLE dl.{name} SET PARTITIONED BY ({identifier(column)})"
            )


def delete_predicate(partition: PhysicalPartition) -> str:
    """Render one safe partition predicate."""
    return partition_condition(partition).as_string(None)


async def commit_table(
    duckdb: DuckDB,
    table: TableConfig,
    paths: list[str],
    partitioned: PartitionedTablePlan | None,
) -> None:
    """Replace or append scratch files in one DuckLake transaction."""
    if not paths:
        return

    name = identifier(table.table_name)
    await ensure_table(duckdb, table, paths[0], partitioned)

    if partitioned is None or partitioned.full_rebuild:
        await duckdb.execute(f"DELETE FROM dl.{name}")
    else:
        affected = list(partitioned.changed_paths) + list(
            partitioned.removed_partitions
        )
        for partition_id in affected:
            physical = partitioned.current_partitions.get(
                partition_id
            ) or partitioned.removed_partitions.get(partition_id)
            if physical is not None:
                await duckdb.execute(
                    f"DELETE FROM dl.{name} WHERE {delete_predicate(physical)}"
                )

    for path in paths:
        await duckdb.execute(
            f"INSERT INTO dl.{name} SELECT * FROM read_parquet(?)", [path]
        )


async def expire_ducklake_snapshots(schemas: set[str]) -> None:
    """Expire old DuckLake snapshots and clean unreferenced Parquet files."""
    age = settings.DUCKLAKE_SNAPSHOT_EXPIRATION
    interval = f"{age.removesuffix('d')} days"
    for schema in sorted(schemas):
        paths = DuckLakePaths.for_schema(schema)
        paths.catalog.parent.mkdir(parents=True, exist_ok=True)
        async with DuckDB.connect() as duckdb:
            await duckdb.execute(
                f"ATTACH 'ducklake:sqlite:{paths.catalog}' AS dl (DATA_PATH '{paths.data}', DATA_INLINING_ROW_LIMIT 0)"
            )
            await duckdb.execute(
                f"CALL ducklake_expire_snapshots('dl', older_than => now() - INTERVAL '{interval}')"
            )
            await duckdb.execute(
                f"CALL ducklake_cleanup_old_files('dl', older_than => now() - INTERVAL '{interval}')"
            )


async def run_ducklake_publication(
    pg_conn: AsyncConnection,
    dbos_conn: AsyncConnection,
    config: SyncConfig,
    plan: SyncPlan,
    failed_paths: set[str] | None = None,
) -> PublicationResult:
    """Publish one schema sequentially into its local SQLite catalog."""
    decision = reduce_sync_plan(plan, failed_paths or set())
    changed = SyncPublicationInput(config=config, plan=plan).changed_tables
    empty = empty_incremental_tables(decision.plan)
    eligible = changed - decision.blocked_tables - empty
    tables = {table.name: table for table in config.tables}
    attempted_at = Instant.now()

    for name in decision.blocked_tables | empty:
        await emit_error(dbos_conn, "table_blocked", table=name)

    paths = DuckLakePaths.for_schema(plan.schema_name)
    published: set[str] = set()

    paths.catalog.parent.mkdir(parents=True, exist_ok=True)
    async with DuckDB.connect() as duckdb:
        await duckdb.execute(
            f"ATTACH 'ducklake:sqlite:{paths.catalog}' AS dl (DATA_PATH '{paths.data}', DATA_INLINING_ROW_LIMIT 0)"
        )
        await duckdb.execute(
            f"CALL dl.set_option('target_file_size', '{settings.DUCKLAKE_TARGET_FILE_SIZE}')"
        )
        await duckdb.execute("BEGIN")
        try:
            for name in sorted(eligible):
                table = tables[name]
                partitioned = decision.plan.partitioned_tables.get(name)
                await commit_table(
                    duckdb,
                    table,
                    planned_paths(decision.plan, name, partitioned),
                    partitioned,
                )
                published.add(name)
            await duckdb.execute("COMMIT")
        except Exception:
            await duckdb.execute("ROLLBACK")
            raise

    for name in published:
        await update_published_freshness(
            pg_conn,
            tables[name],
            decision.plan,
            decision.failed_partitions.get(name, set()),
            attempted_at,
        )
    await pg_conn.commit()

    return PublicationResult(plan=decision.plan, published_tables=published)
