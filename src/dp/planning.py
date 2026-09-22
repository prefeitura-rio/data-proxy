"""Change detection and task planning for synchronization runs."""

from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps

from more_itertools import constrained_batches
from psycopg import AsyncConnection
from psycopg.sql import Literal

from .bigquery.clients import BigQuery
from .bigquery.partitions import physical_partitions, table_modified
from .duckdb import DuckDB
from .executor import execute_sql
from .log import logger
from .models import (
    AllSelection,
    DumpTask,
    PartitionedTable,
    PartitionedTablePlan,
    PartitionManifest,
    PhysicalPartition,
    Strategy,
    SyncConfig,
    SyncPlan,
    SyncWork,
    TableConfig,
)
from .settings import settings
from .state import read_partition_manifest, read_table_signature


@dataclass(frozen=True, slots=True)
class PartitionChanges:
    """Physical partition changes for one table."""

    full_rebuild: bool
    changed: set[str]
    removed: set[str]
    previous: dict[str, PhysicalPartition]


@dataclass(frozen=True, slots=True)
class PartitionTaskBatch:
    """Extraction paths and tasks for changed physical partitions."""

    paths: dict[str, str]
    tasks: list[DumpTask]


async def discover_json_columns(duckdb_conn: DuckDB, bq_table: str) -> list[str]:
    """Return column names whose DuckDB type contains STRUCT."""
    rows = await execute_sql(
        duckdb_conn, "duckdb/describe_table", {"bq_table": Literal(bq_table)}
    )

    return [str(row[0]) for row in rows if "STRUCT" in str(row[1]).upper()]


async def expand_config(
    tables: list[TableConfig],
    s3_bucket: str,
    sync_id: str,
    duckdb_conn: DuckDB,
) -> list[DumpTask]:
    """Expand full tables into whole-table extraction tasks."""
    tasks: list[DumpTask] = []

    for table in tables:
        if table.strategy != Strategy.FULL:
            continue

        json_columns = await discover_json_columns(duckdb_conn, table.name)

        tasks.append(
            table.to_task(
                sync_id,
                s3_bucket,
                [AllSelection()],
                json_columns=json_columns,
            )
        )

    return tasks


def table_signature(table: TableConfig, claim: str | None, modified: str) -> str:
    """Combine source modification time with table and schema configuration."""
    config_fields = table.config_signature_fields()
    config_fields["claim"] = claim

    config_hash = sha256(dumps(config_fields, sort_keys=True).encode()).hexdigest()

    return f"{modified}:{config_hash}"


async def detect_changes(
    config: SyncConfig, pg_conn: AsyncConnection
) -> dict[str, str]:
    """Return full table signatures changed since their successful sync."""
    changed: dict[str, str] = {}
    full_tables = [t for t in config.tables if t.strategy == Strategy.FULL]

    by_project: dict[str, list[TableConfig]] = {}
    for table in full_tables:
        by_project.setdefault(table.name.split(".")[0], []).append(table)

    for project, tables in by_project.items():
        async with BigQuery.connect(project) as bq_conn:
            for table in tables:
                modified = await table_modified(bq_conn, table.name)
                claim = config.schemas[table.resolved_schema].claim
                current = table_signature(table, claim, modified)

                stored = await read_table_signature(pg_conn, table.name)

                if stored != current:
                    changed[table.name] = current

    return changed


def find_partition_changes(
    current: dict[str, PhysicalPartition],
    stored: PartitionManifest | None,
    table_signature: str,
) -> PartitionChanges:
    """Return changed physical partition state."""
    first_sync = stored is None
    table_changed = stored is not None and stored.table_signature != table_signature
    full_rebuild = first_sync or table_changed
    previous = stored.partitions if stored is not None else {}

    changed = {
        partition_id
        for partition_id, partition in current.items()
        if full_rebuild
        or partition_id not in previous
        or previous[partition_id].signature != partition.signature
    }

    return PartitionChanges(
        full_rebuild=full_rebuild,
        changed=changed,
        removed=previous.keys() - current.keys(),
        previous=previous,
    )


def order_partition_ids(changed: set[str]) -> list[str]:
    """Return changed partition ids in publication order with __NULL__ last."""
    return sorted(
        changed,
        key=lambda partition_id: (
            (1, "") if not partition_id.isdigit() else (0, int(partition_id))
        ),
    )


def group_partitions(
    ordered: list[str],
    current: dict[str, PhysicalPartition],
    target_bytes: int,
    max_partitions: int,
) -> list[list[str]]:
    """Group partitions by byte size and item count, closing before a limit is exceeded."""
    return [
        list(batch)
        for batch in constrained_batches(
            ordered,
            max_size=target_bytes,
            max_count=max_partitions,
            get_len=lambda partition_id: current[partition_id].logical_bytes,
            strict=False,
        )
    ]


def build_partition_tasks(
    table: PartitionedTable,
    current: dict[str, PhysicalPartition],
    changed: set[str],
    sync_id: str,
    s3_bucket: str,
    json_columns: list[str],
) -> PartitionTaskBatch:
    """Create one task and path for each batch of changed physical partitions."""
    batches = group_partitions(
        order_partition_ids(changed),
        current,
        settings.DUMPER_BATCH_BYTES,
        settings.DUMPER_BATCH_MAX_PARTITIONS,
    )

    tasks = [
        table.to_task(
            sync_id,
            s3_bucket,
            [current[partition_id].selection for partition_id in batch],
            f"batches/{index}",
            json_columns,
        )
        for index, batch in enumerate(batches)
    ]

    paths = {
        partition_id: task.bucket_path
        for batch, task in zip(batches, tasks, strict=True)
        for partition_id in batch
    }

    return PartitionTaskBatch(paths=paths, tasks=tasks)


async def plan_partitioned_table(
    table: PartitionedTable,
    bq_conn: BigQuery,
    pg_conn: AsyncConnection,
    sync_id: str,
    s3_bucket: str,
    duckdb_conn: DuckDB,
) -> tuple[PartitionedTablePlan | None, list[DumpTask]]:
    """Plan one physically partitioned table."""
    table_sig, current = await physical_partitions(
        bq_conn, table.name, table.model_dump_json(), table.n
    )
    stored = await read_partition_manifest(pg_conn, table.name)
    changes = find_partition_changes(current, stored, table_sig)

    if not changes.changed and not changes.removed:
        return None, []

    json_columns = await discover_json_columns(duckdb_conn, table.name)
    batch = build_partition_tasks(
        table,
        current,
        changes.changed,
        sync_id,
        s3_bucket,
        json_columns,
    )

    plan = PartitionedTablePlan(
        table_signature=table_sig,
        full_rebuild=changes.full_rebuild,
        current_partitions=current,
        changed_paths=batch.paths,
        previous_partitions={
            partition_id: changes.previous[partition_id]
            for partition_id in changes.changed
            if partition_id in changes.previous
        },
        removed_partitions={
            partition_id: changes.previous[partition_id]
            for partition_id in changes.removed
        },
    )

    return plan, batch.tasks


async def plan_partitioned_tables(
    config: SyncConfig,
    pg_conn: AsyncConnection,
    sync_id: str,
    s3_bucket: str,
    duckdb_conn: DuckDB,
) -> tuple[dict[str, PartitionedTablePlan], list[DumpTask]]:
    """Plan changed physical partitions for all partitioned tables."""
    plans: dict[str, PartitionedTablePlan] = {}
    tasks: list[DumpTask] = []

    partitioned_tables = [
        t for t in config.tables if t.strategy == Strategy.PARTITIONED
    ]

    by_project: dict[str, list[PartitionedTable]] = {}
    for table in partitioned_tables:
        by_project.setdefault(table.name.split(".")[0], []).append(table)

    for project, tables in by_project.items():
        async with BigQuery.connect(project) as bq_conn:
            for table in tables:
                plan, table_tasks = await plan_partitioned_table(
                    table, bq_conn, pg_conn, sync_id, s3_bucket, duckdb_conn
                )

                if plan:
                    plans[table.name] = plan
                    tasks.extend(table_tasks)

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

    config: SyncConfig
    pg_conn: AsyncConnection
    sync_id: str
    bucket: str
    duckdb_conn: DuckDB
    changed: dict[str, str] = field(default_factory=dict)
    tasks: list[DumpTask] = field(default_factory=list)
    signatures: dict[str, str] = field(default_factory=dict)
    paths: dict[str, list[str]] = field(default_factory=dict)
    partitioned: dict[str, PartitionedTablePlan] = field(default_factory=dict)

    async def detect(self) -> None:
        """Detect changed full table signatures."""
        self.changed = await detect_changes(self.config, self.pg_conn)
        logger.info("Detected %d changed full tables", len(self.changed))

    async def expand(self) -> None:
        """Expand changed full tables into extraction tasks."""
        changed_tables = [
            table for table in self.config.tables if table.name in self.changed
        ]
        self.tasks = await expand_config(
            changed_tables, self.bucket, self.sync_id, self.duckdb_conn
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
            self.config,
            self.pg_conn,
            self.sync_id,
            self.bucket,
            self.duckdb_conn,
        )
        self.tasks.extend(partition_tasks)

    def group(self) -> SyncWork:
        """Group full and partitioned table plans by resolved schema."""
        if not self.signatures and not self.partitioned:
            logger.info("No changes to plan")
            return SyncWork(plans=[], tasks=[])

        logger.info(
            "Built sync plan with %d full and %d partitioned tables",
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
    config: SyncConfig,
    pg_conn: AsyncConnection,
    sync_id: str,
    bucket: str,
    duckdb_conn: DuckDB,
) -> SyncWork:
    """Build a publisher plan and tasks for changed data only."""
    ctx = PlanningContext(
        config=config,
        pg_conn=pg_conn,
        sync_id=sync_id,
        bucket=bucket,
        duckdb_conn=duckdb_conn,
    )

    await ctx.detect()
    await ctx.expand()
    await ctx.plan_partitions()
    return ctx.group()
