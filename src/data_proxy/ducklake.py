"""Publish scratch Parquet files into per-schema DuckLake catalogs."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never
from urllib.parse import quote

import psycopg
from psycopg.rows import TupleRow
from psycopg.sql import Composable, Identifier, Literal

from .catalog import CatalogPaths
from .conditions import partition_condition
from .duckdb import DuckDB
from .executor import Executor
from .models import (
    DuckLakePartition,
    DuckLakePartitionTransform,
    PartitionedTablePlan,
    PublicationResult,
    SyncConfig,
    SyncPlan,
    TableConfig,
)
from .postgres import Postgres
from .settings import settings
from .sources.partitions import (
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from .state import emit_error
from .types import DatabaseRow, DuckDBParams, PostgresParams


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
            data=f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{quote(schema, safe='')}",
        )

    def prepare_catalog_directory(self) -> None:
        self.catalog.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        self.catalog.parent.chmod(0o755)


def planned_paths(
    plan: SyncPlan, table: str, partitioned: PartitionedTablePlan | None
) -> list[str]:
    """Return scratch paths for one table."""
    match partitioned:
        case PartitionedTablePlan():
            return [
                change.path
                for change in partitioned.changes.values()
                if change.kind in ("add", "update")
            ]
        case None:
            return plan.paths.get(table, [])
        case _:
            assert_never(partitioned)


def empty_incremental_tables(plan: SyncPlan) -> set[str]:
    """Return incremental tables with no data changes."""
    return {
        name
        for name, table_plan in plan.partitioned_tables.items()
        if not table_plan.full_rebuild and not table_plan.changes
    }


def failed_partition_ids(
    table_plan: PartitionedTablePlan, failed_paths: set[str]
) -> set[str]:
    """Return the partitions whose extraction output failed."""
    return {
        partition_id
        for partition_id, change in table_plan.changes.items()
        if change.kind in ("add", "update") and change.path in failed_paths
    }


def restore_partition(table_plan: PartitionedTablePlan, partition_id: str) -> None:
    """Roll one partition back to its previous state."""
    change = table_plan.changes.pop(partition_id)
    previous = change.previous
    match previous:
        case None:
            table_plan.current_partitions.pop(partition_id, None)
        case PhysicalPartition():
            table_plan.current_partitions[partition_id] = previous


def plan_publication(
    plan: SyncPlan, failed_paths: set[str]
) -> tuple[SyncPlan, set[str], dict[str, set[str]]]:
    """Remove failed extraction outputs from a publication plan."""
    reduced = plan.model_copy(deep=True)
    blocked = {
        table
        for table, paths in plan.paths.items()
        if any(path in failed_paths for path in paths)
    }

    failed_partitions: dict[str, set[str]] = {}

    for table, table_plan in reduced.partitioned_tables.items():
        failed_ids = failed_partition_ids(table_plan, failed_paths)

        if not failed_ids:
            continue

        failed_partitions[table] = failed_ids

        if table_plan.full_rebuild:
            blocked.add(table)
        else:
            for partition_id in failed_ids:
                restore_partition(table_plan, partition_id)

    return reduced, blocked, failed_partitions


def custom_partitioning_expression(partitions: list[DuckLakePartition]) -> str:
    """Render configured DuckLake partition transforms."""
    expressions: list[str] = []
    for partition in partitions:
        column = Identifier(partition.column).as_string(None)
        match partition.transform:
            case DuckLakePartitionTransform.IDENTITY:
                expressions.append(column)
            case DuckLakePartitionTransform.BUCKET:
                expressions.append(f"bucket({partition.buckets}, {column})")
            case DuckLakePartitionTransform.YEAR:
                expressions.append(f"year({column})")
            case DuckLakePartitionTransform.MONTH:
                expressions.append(f"month({column})")
            case DuckLakePartitionTransform.DAY:
                expressions.append(f"day({column})")
            case DuckLakePartitionTransform.HOUR:
                expressions.append(f"hour({column})")
            case _:
                assert_never(partition.transform)
    return ", ".join(expressions)


def source_partition_column(partitioned: PartitionedTablePlan) -> str | None:
    """Return the source partition column, if any partitions exist."""
    if not partitioned.current_partitions:
        return None
    selection = next(iter(partitioned.current_partitions.values())).selection
    match selection:
        case TimeRangeSelection(column=column):
            return Identifier(column).as_string(None)
        case RangeSelection(column=column):
            return Identifier(column).as_string(None)
        case RemainderSelection(column=column):
            return Identifier(column).as_string(None)
        case _:
            assert_never(selection)


async def evolve_table_schema(
    duckdb_conn: DuckDB, table_name: str, parquet_path: str
) -> None:
    """Apply the source schema to an existing table.

    The source schema is authoritative: removed columns are dropped and any
    type change goes directly to DuckDB, which fails when the cast is invalid.
    """
    existing_rows = await Executor[DuckDBParams, list[DatabaseRow]](
        conn=duckdb_conn
    ).query(
        "duckdb/describe_table",
        mapping={"table": Identifier(table_name)},
        expect=tuple[str, str],
    )

    source_rows = await Executor[DuckDBParams, list[DatabaseRow]](
        conn=duckdb_conn
    ).query(
        "duckdb/describe_parquet",
        params=[parquet_path],
        expect=tuple[str, str],
    )

    existing = {row[0]: row[1] for row in existing_rows}
    source = {row[0]: row[1] for row in source_rows}

    alterations: list[dict[str, str]] = [
        {
            "operation": "drop",
            "column": Identifier(column).as_string(None),
            "type": "",
        }
        for column in sorted(existing.keys() - source.keys())
    ]

    for column, source_type in source.items():
        existing_type = existing.get(column)

        if existing_type is None:
            alterations.append(
                {
                    "operation": "add",
                    "column": Identifier(column).as_string(None),
                    "type": source_type,
                }
            )
            continue

        if existing_type.upper() == source_type.upper():
            continue

        alterations.append(
            {
                "operation": "promote",
                "column": Identifier(column).as_string(None),
                "type": source_type,
            }
        )

    for alteration in alterations:
        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).execute(
            "duckdb/alter_column",
            mapping={
                "table": Identifier(table_name),
                "alterations": [alteration],
            },
        )


async def current_sort(duckdb_conn: DuckDB, table_name: str) -> list[str]:
    """Return the active DuckLake sort columns for a table."""
    rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).query(
        "duckdb/get_sort",
        params=[table_name],
        expect=tuple[str],
    )

    return [row[0].strip('"') for row in rows]


async def table_exists(duckdb_conn: DuckDB, table_name: str) -> bool:
    """Return whether a DuckLake table already exists."""
    rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).query(
        "duckdb/table_exists",
        params=[table_name],
        expect=tuple[int],
    )

    return bool(rows and rows[0][0] > 0)


def validate_sort_columns(table: TableConfig, source_columns: set[str]) -> None:
    """Fail when configured sort columns are not in the source schema."""
    unknown = set(table.ducklake.sort or []) - source_columns
    if unknown:
        raise ValueError(f"Unknown DuckLake sort columns: {', '.join(sorted(unknown))}")


async def apply_sort(
    duckdb_conn: DuckDB,
    name: Composable,
    table: TableConfig,
    path: str,
    existing: bool,
) -> None:
    """Set or reset the DuckLake sort order to match the configuration."""
    configured_sort = table.ducklake.sort or []
    if configured_sort:
        source_rows = await Executor[DuckDBParams, list[DatabaseRow]](
            conn=duckdb_conn
        ).query(
            "duckdb/describe_parquet",
            params=[path],
            expect=tuple[str, str],
        )
        validate_sort_columns(table, {row[0] for row in source_rows})

    active_sort = await current_sort(duckdb_conn, table.table_name) if existing else []

    if active_sort != configured_sort and (configured_sort or existing):
        sort_columns = ", ".join(
            Identifier(column).as_string(None) for column in configured_sort
        )

        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).execute(
            "duckdb/set_sorted_by",
            mapping={"table": name, "sort_columns": sort_columns},
        )


async def apply_partitioning(
    duckdb_conn: DuckDB,
    name: Composable,
    table: TableConfig,
    partitioned: PartitionedTablePlan | None,
    existing: bool,
) -> None:
    """Set or reset DuckLake partitioning to match the configuration."""
    if table.ducklake.partitioning:
        partitioning = custom_partitioning_expression(table.ducklake.partitioning)
    elif partitioned is not None:
        partitioning = source_partition_column(partitioned)
    else:
        partitioning = None

    if partitioning is not None or (existing and partitioned is None):
        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).execute(
            "duckdb/set_partitioned_by",
            mapping={"table": name, "partitioning": partitioning or ""},
        )


async def ensure_table(
    duckdb_conn: DuckDB,
    table: TableConfig,
    path: str,
    partitioned: PartitionedTablePlan | None,
) -> None:
    """Create or evolve a DuckLake table and apply sort and partitioning."""
    name = Identifier(table.table_name)
    existing = await table_exists(duckdb_conn, table.table_name)

    if existing:
        await evolve_table_schema(duckdb_conn, table.table_name, path)
    else:
        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).execute(
            "duckdb/create_table",
            mapping={"table": name},
            params=[path],
        )

    await apply_sort(duckdb_conn, name, table, path, existing)
    await apply_partitioning(duckdb_conn, name, table, partitioned, existing)


def delete_predicate(partition: PhysicalPartition) -> str:
    """Render one safe partition predicate."""
    return partition_condition(partition).as_string(None)


def affected_predicates(partitioned: PartitionedTablePlan) -> list[str]:
    """Return delete predicates for changed and removed partitions."""
    predicates: list[str] = []

    for change in partitioned.changes.values():
        physical = change.current or change.previous

        if physical is not None:
            predicates.append(delete_predicate(physical))

    return predicates


async def delete_existing_rows(
    duckdb_conn: DuckDB,
    name: Composable,
    partitioned: PartitionedTablePlan | None,
) -> None:
    """Delete rows that the new files replace."""
    if partitioned is None or partitioned.full_rebuild:
        predicate = ""
    else:
        predicates = affected_predicates(partitioned)

        if not predicates:
            return

        predicate = " OR ".join(predicates)

    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).execute(
        "duckdb/delete_partition",
        mapping={"table": name, "predicate": predicate},
    )


async def commit_table(
    duckdb_conn: DuckDB,
    table: TableConfig,
    paths: list[str],
    partitioned: PartitionedTablePlan | None,
) -> None:
    """Replace or append scratch files in one DuckLake transaction."""
    if not paths and (
        partitioned is None
        or (not partitioned.full_rebuild and not partitioned.changes)
    ):
        return

    name = Identifier(table.table_name)
    if paths:
        await ensure_table(duckdb_conn, table, paths[0], partitioned)

    await delete_existing_rows(duckdb_conn, name, partitioned)

    if paths:
        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).execute(
            "duckdb/insert_parquet",
            mapping={"table": name},
            params=[paths],
        )


async def attach_catalog(
    duckdb_conn: DuckDB, paths: DuckLakePaths, encrypted: bool
) -> None:
    """Attach one per-schema DuckLake catalog for writing."""
    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).execute(
        "duckdb/attach",
        mapping={
            "catalog": Literal(f"ducklake:sqlite:{paths.catalog}"),
            "data_path": Literal(paths.data),
            "encrypted": encrypted,
        },
    )


async def configure_catalog(
    duckdb_conn: DuckDB, paths: DuckLakePaths, encrypted: bool
) -> None:
    """Attach one per-schema DuckLake catalog and set its file-size option."""
    await attach_catalog(duckdb_conn, paths, encrypted)
    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).execute(
        "duckdb/set_option",
        mapping={
            "option": "target_file_size",
            "value": Literal(settings.DUCKLAKE_TARGET_FILE_SIZE),
        },
    )


async def emit_blocked_errors(
    pg_conn: Postgres, blocked: set[str], empty: set[str]
) -> None:
    """Record one blocked-table error for each table that will not publish."""
    for name in blocked | empty:
        await emit_error(pg_conn, "table_blocked", table=name)


async def publish_tables(
    duckdb_conn: DuckDB,
    config: SyncConfig,
    plan: SyncPlan,
    eligible: set[str],
) -> set[str]:
    """Commit eligible tables into one attached DuckLake catalog."""
    tables = {table.name: table for table in config.tables}
    published: set[str] = set()

    for name in sorted(eligible):
        table = tables[name]
        partitioned = plan.partitioned_tables.get(name)
        await commit_table(
            duckdb_conn,
            table,
            planned_paths(plan, name, partitioned),
            partitioned,
        )
        published.add(name)
    return published


async def current_snapshot_id(duckdb_conn: DuckDB) -> int:
    """Return the current snapshot ID of the attached DuckLake catalog."""
    rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).query(
        "duckdb/current_snapshot",
        expect=tuple[int],
    )
    return rows[0][0]


async def reader_snapshot(pg_conn: Postgres, schema_name: str) -> int | None:
    """Return the snapshot the reader catalog has applied, or None when unavailable."""
    try:
        rows = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
            "postgres/reader_snapshot",
            mapping={"schema": Identifier(schema_name)},
            expect=tuple[int | None],
        )
    except psycopg.Error:
        await pg_conn.rollback()
        return None

    await pg_conn.commit()
    return rows[0][0]


async def wait_for_reader(
    read_snapshot: Callable[[], Awaitable[int | None]],
    snapshot_id: int,
    *,
    timeout: float,
    interval: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until the reader catalog has applied at least the given snapshot."""
    deadline = clock() + timeout

    while True:
        current = await read_snapshot()

        if current is not None and current >= snapshot_id:
            return

        if clock() >= deadline:
            raise TimeoutError(
                f"Reader missed snapshot {snapshot_id} for {timeout:g} seconds"
            )

        await sleep(interval)


async def publish_schema(
    duckdb_conn: DuckDB,
    pg_conn: Postgres,
    config: SyncConfig,
    plan: SyncPlan,
    failed_paths: set[str],
) -> PublicationResult:
    """Publish one schema sequentially into its local SQLite catalog."""
    reduced_plan, blocked_tables, _failed = plan_publication(plan, failed_paths)
    changed = plan.signatures.keys() | plan.partitioned_tables.keys()
    empty = empty_incremental_tables(reduced_plan)
    eligible = changed - blocked_tables - empty

    await emit_blocked_errors(pg_conn, blocked_tables, empty)

    paths = DuckLakePaths.for_schema(plan.schema_name)
    paths.prepare_catalog_directory()

    await configure_catalog(
        duckdb_conn,
        paths,
        config.schemas[plan.schema_name].ducklake.encrypted,
    )
    published = await publish_tables(duckdb_conn, config, reduced_plan, eligible)

    return PublicationResult(
        plan=reduced_plan,
        published_tables=published,
        snapshot_id=await current_snapshot_id(duckdb_conn) if published else None,
    )
