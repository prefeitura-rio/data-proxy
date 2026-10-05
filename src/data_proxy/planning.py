"""Change detection and task planning for synchronization runs."""

from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps
from typing import assert_never

from .duckdb import DuckDB
from .executor import Executor
from .log import logger
from .models import (
    DumpTask,
    FullTable,
    PartitionChange,
    PartitionedTable,
    PartitionedTablePlan,
    Strategy,
    SyncConfig,
    SyncPlan,
    SyncWork,
    TableConfig,
    TableState,
)
from .postgres import Postgres
from .settings import settings
from .sources import registry
from .sources.partitions import AllSelection, PartitionRequest, PhysicalPartition
from .sources.source import PartitionedSource, Source
from .state import read_table_signature, read_table_state
from .types import DatabaseRow, DuckDBParams


def partition_sort_key(partition_id: str) -> tuple[int, int | str]:
    """Return the publication order key with __NULL__ last."""
    return (1, "") if not partition_id.isdigit() else (0, int(partition_id))


def configured_source(table: TableConfig, active: dict[str, Source]) -> Source:
    """Return the cached configured source for one table schema."""
    source = active.get(table.resolved_schema)

    if source is None:
        source = registry.sources.configure(
            table.resolved_source, table.resolved_source_settings
        )
        active[table.resolved_schema] = source
    return source


async def close_sources(active: dict[str, Source]) -> None:
    """Close every source client cache after one planning phase."""
    for source in active.values():
        await source.close()


async def discover_json_columns(
    duckdb_conn: DuckDB, source: Source, table: str
) -> list[str]:
    """Return column names whose DuckDB type contains STRUCT."""
    rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb_conn).query(
        "duckdb/describe_source",
        {"source": source.scan(table)},
        expect=tuple[str, str],
    )

    return [row[0] for row in rows if "STRUCT" in row[1].upper()]


async def expand_config(
    duckdb_conn: DuckDB,
    tables: list[TableConfig],
    s3_bucket: str,
    sync_id: str,
) -> list[DumpTask]:
    """Expand full tables into whole-table extraction tasks."""
    tasks: list[DumpTask] = []
    active: dict[str, Source] = {}

    try:
        for table in tables:
            match table.strategy:
                case Strategy.FULL:
                    source = configured_source(table, active)

                    json_columns = await discover_json_columns(
                        duckdb_conn, source, table.name
                    )

                    tasks.append(
                        table.to_task(
                            sync_id,
                            s3_bucket,
                            settings.S3_SCRATCH_PREFIX,
                            [AllSelection()],
                            json_columns=json_columns,
                        )
                    )
                case Strategy.PARTITIONED:
                    continue
                case _:
                    assert_never(table.strategy)
    finally:
        await close_sources(active)

    return tasks


def table_signature(table: TableConfig, claim: str | None, modified: str) -> str:
    """Combine source modification time with table and schema configuration."""
    config_fields = table.config_signature_fields()
    config_fields["claim"] = claim

    config_hash = sha256(dumps(config_fields, sort_keys=True).encode()).hexdigest()

    return f"{modified}:{config_hash}"


async def detect_changes(pg_conn: Postgres, config: SyncConfig) -> dict[str, str]:
    """Return full table signatures changed since their successful sync."""
    changed: dict[str, str] = {}
    full_tables: list[FullTable] = []
    for table in config.tables:
        match table.strategy:
            case Strategy.FULL:
                full_tables.append(table)
            case Strategy.PARTITIONED:
                continue
            case _:
                assert_never(table.strategy)

    active: dict[str, Source] = {}
    try:
        for table in full_tables:
            source = configured_source(table, active)
            modified = await source.modified(table.name)
            claim = config.schemas[table.resolved_schema].claim

            current = table_signature(table, claim, modified)
            stored = await read_table_signature(pg_conn, table.name)

            if stored != current:
                changed[table.name] = current
    finally:
        await close_sources(active)

    return changed


def find_partition_changes(
    current: dict[str, PhysicalPartition],
    stored: TableState | None,
    table_signature: str,
) -> dict[str, PartitionChange]:
    """Return one PartitionChange per added, updated, or removed partition."""
    previous = (
        stored.partitions
        if stored is not None and stored.partitions is not None
        else {}
    )
    first_sync = stored is None
    table_changed = stored is not None and stored.signature != table_signature

    changes: dict[str, PartitionChange] = {}

    for partition_id, partition in current.items():
        prior = previous.get(partition_id)
        is_new = prior is None
        signature_changed = prior is not None and prior.signature != partition.signature

        if is_new or first_sync or table_changed or signature_changed:
            changes[partition_id] = PartitionChange(
                kind="add" if is_new else "update",
                partition_id=partition_id,
                current=partition,
                previous=prior,
            )

    for partition_id in previous.keys() - current.keys():
        changes[partition_id] = PartitionChange(
            kind="remove",
            partition_id=partition_id,
            previous=previous[partition_id],
        )

    return changes


def order_partition_ids(changed: set[str]) -> list[str]:
    """Return changed partition ids in publication order with __NULL__ last."""
    return sorted(changed, key=partition_sort_key)


def build_partition_tasks(
    table: PartitionedTable,
    current: dict[str, PhysicalPartition],
    changes: dict[str, PartitionChange],
    sync_id: str,
    s3_bucket: str,
    json_columns: list[str],
) -> tuple[dict[str, PartitionChange], DumpTask]:
    """Fill extraction paths on add/update changes and return the updated diff and task."""
    extractable_ids = order_partition_ids(
        {pid for pid, c in changes.items() if c.kind in ("add", "update")}
    )

    task = table.to_task(
        sync_id,
        s3_bucket,
        settings.S3_SCRATCH_PREFIX,
        [current[partition_id].selection for partition_id in extractable_ids],
        "batches/0",
        json_columns,
    )

    updated = changes | {
        partition_id: changes[partition_id].model_copy(update={"path": path})
        for partition_id, path in zip(extractable_ids, task.output_paths, strict=True)
    }

    return updated, task


async def plan_partitioned_table(
    source: PartitionedSource,
    pg_conn: Postgres,
    duckdb_conn: DuckDB,
    table: PartitionedTable,
    sync_id: str,
    s3_bucket: str,
) -> tuple[PartitionedTablePlan | None, list[DumpTask]]:
    """Plan one physically partitioned table."""
    table_sig, current = await source.partitions(
        PartitionRequest(
            table=table.name,
            config=table.model_dump(mode="json"),
            keep_latest=table.n,
        )
    )
    stored = await read_table_state(pg_conn, table.name)
    changes = find_partition_changes(current, stored, table_sig)

    if not changes:
        return None, []

    tasks: list[DumpTask] = []
    if any(c.kind in ("add", "update") for c in changes.values()):
        json_columns = await discover_json_columns(duckdb_conn, source, table.name)
        changes, task = build_partition_tasks(
            table,
            current,
            changes,
            sync_id,
            s3_bucket,
            json_columns,
        )
        tasks.append(task)

    full_rebuild = stored is None or stored.signature != table_sig

    plan = PartitionedTablePlan(
        table_signature=table_sig,
        full_rebuild=full_rebuild,
        current_partitions=current,
        changes=changes,
    )

    return plan, tasks


async def plan_partitioned_tables(
    pg_conn: Postgres,
    duckdb_conn: DuckDB,
    config: SyncConfig,
    sync_id: str,
    s3_bucket: str,
) -> tuple[dict[str, PartitionedTablePlan], list[DumpTask]]:
    """Plan changed physical partitions for all partitioned tables."""
    plans: dict[str, PartitionedTablePlan] = {}
    tasks: list[DumpTask] = []

    partitioned_tables: list[PartitionedTable] = []
    for table in config.tables:
        match table.strategy:
            case Strategy.FULL:
                continue
            case Strategy.PARTITIONED:
                partitioned_tables.append(table)
            case _:
                assert_never(table.strategy)

    active: dict[str, Source] = {}
    try:
        for table in partitioned_tables:
            source = configured_source(table, active)
            if not isinstance(source, PartitionedSource):
                raise TypeError(
                    f"Source {source.name!r} does not support partitioned tables"
                )
            plan, table_tasks = await plan_partitioned_table(
                source, pg_conn, duckdb_conn, table, sync_id, s3_bucket
            )

            if plan is not None:
                plans[table.name] = plan
                tasks.extend(table_tasks)
    finally:
        await close_sources(active)

    return plans, tasks


def group_schema_plans(
    config: SyncConfig,
    signatures: dict[str, str],
    paths: dict[str, list[str]],
    partitioned: dict[str, PartitionedTablePlan],
) -> list[SyncPlan]:
    """Group full and partitioned table plans by resolved schema."""
    schema_names = {table.name: table.resolved_schema for table in config.tables}
    grouped: dict[str, SyncPlan] = {}

    for table, signature in signatures.items():
        schema_name = schema_names[table]
        schema_plan = grouped.setdefault(schema_name, SyncPlan(schema_name=schema_name))
        schema_plan.signatures[table] = signature
        schema_plan.paths[table] = paths[table]

    for table, partition_plan in partitioned.items():
        schema_name = schema_names[table]
        grouped.setdefault(
            schema_name, SyncPlan(schema_name=schema_name)
        ).partitioned_tables[table] = partition_plan

    return list(grouped.values())


@dataclass
class PlanningContext:
    """Pipeline state for one synchronization planning run."""

    pg_conn: Postgres
    duckdb_conn: DuckDB
    config: SyncConfig
    sync_id: str
    bucket: str
    changed: dict[str, str] = field(default_factory=dict)
    tasks: list[DumpTask] = field(default_factory=list)
    signatures: dict[str, str] = field(default_factory=dict)
    paths: dict[str, list[str]] = field(default_factory=dict)
    partitioned: dict[str, PartitionedTablePlan] = field(default_factory=dict)

    async def detect(self) -> None:
        """Detect changed full table signatures."""
        self.changed = await detect_changes(self.pg_conn, self.config)
        logger.info(
            "Full table change detection completed: changed_tables=%d",
            len(self.changed),
        )

    async def expand(self) -> None:
        """Expand changed full tables into extraction tasks."""
        changed_tables = [
            table for table in self.config.tables if table.name in self.changed
        ]
        self.tasks = await expand_config(
            self.duckdb_conn, changed_tables, self.bucket, self.sync_id
        )

        tables = {task.table for task in self.tasks}
        self.signatures = {
            table: signature
            for table, signature in self.changed.items()
            if table in tables
        }

        for task in self.tasks:
            self.paths.setdefault(task.table, []).append(task.bucket_path)

    async def plan_partitions(self) -> None:
        """Plan changed physical partitions for all partitioned tables."""
        self.partitioned, partition_tasks = await plan_partitioned_tables(
            self.pg_conn,
            self.duckdb_conn,
            self.config,
            self.sync_id,
            self.bucket,
        )
        self.tasks.extend(partition_tasks)

    def group(self) -> SyncWork:
        """Group full and partitioned table plans by resolved schema."""
        if not self.signatures and not self.partitioned:
            logger.info("Sync plan completed: changes=none")
            return SyncWork(plans=[], tasks=[])

        logger.info(
            "Sync plan completed: full_tables=%d partitioned_tables=%d",
            len(self.signatures),
            len(self.partitioned),
        )

        return SyncWork(
            plans=group_schema_plans(
                self.config, self.signatures, self.paths, self.partitioned
            ),
            tasks=self.tasks,
        )


async def run_planning(
    pg_conn: Postgres,
    duckdb_conn: DuckDB,
    config: SyncConfig,
    sync_id: str,
    bucket: str,
) -> SyncWork:
    """Build a publisher plan and tasks for changed data only."""
    ctx = PlanningContext(
        pg_conn=pg_conn,
        duckdb_conn=duckdb_conn,
        config=config,
        sync_id=sync_id,
        bucket=bucket,
    )

    await ctx.detect()
    await ctx.expand()
    await ctx.plan_partitions()
    return ctx.group()
