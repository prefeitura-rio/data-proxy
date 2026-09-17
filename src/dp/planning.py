"""Change detection and task planning for synchronization runs."""

from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from typing import cast

from asyncer import asyncify
from duckdb import DuckDBPyConnection
from google.cloud.bigquery import Client
from more_itertools import constrained_batches
from psycopg.sql import Literal
from redis.asyncio import Redis

from .bigquery.clients import bigquery_clients
from .bigquery.partitions import physical_partitions
from .bigquery.tables import table_modified
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


async def discover_json_columns(db: DuckDBPyConnection, bq_table: str) -> list[str]:
    """Return column names whose DuckDB type contains STRUCT."""
    cursor = await execute_sql(
        db,
        "duckdb/describe_table",
        mapping={"bq_table": Literal(bq_table)},
    )
    rows = cast("list[tuple[object, object]]", cursor.fetchall())

    return [str(row[0]) for row in rows if "STRUCT" in str(row[1]).upper()]


async def expand_config(
    tables: list[TableConfig],
    s3_bucket: str,
    sync_id: str,
    db: DuckDBPyConnection,
) -> list[DumpTask]:
    """Expand full tables into whole-table extraction tasks."""
    tasks: list[DumpTask] = []

    for table in tables:
        if table.strategy != Strategy.FULL:
            continue

        json_columns = await discover_json_columns(db, table.name)

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


async def detect_changes(config: SyncConfig, redis: Redis) -> dict[str, str]:
    """Return full table signatures changed since their successful sync."""
    changed: dict[str, str] = {}

    with bigquery_clients() as get_client:
        for table in config.tables:
            if table.strategy != Strategy.FULL:
                continue

            project = table.name.split(".")[0]
            client = get_client(project)

            modified = await asyncify(table_modified)(client, table.name)
            claim = config.schemas[table.resolved_schema].claim
            current = table_signature(table, claim, modified)

            stored = await read_table_signature(redis, table.name)

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
    """Return changed partition ids in publication order.

    Numeric partition ids sort first in ascending order; the remainder
    partition (BigQuery's non-numeric ``__NULL__`` id) always sorts last.
    """
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
    """Group partitions by byte size and item count.

    Close a batch before adding an item that would exceed either limit.
    """
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
    client: Client,
    redis: Redis,
    sync_id: str,
    s3_bucket: str,
    db: DuckDBPyConnection,
) -> tuple[PartitionedTablePlan | None, list[DumpTask]]:
    """Plan one physically partitioned table."""
    table_sig, current = await physical_partitions(
        client, table.name, table.model_dump_json(), table.n
    )
    stored = await read_partition_manifest(redis, table.name)
    changes = find_partition_changes(current, stored, table_sig)

    if not changes.changed and not changes.removed:
        return None, []

    json_columns = await discover_json_columns(db, table.name)
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
    redis: Redis,
    sync_id: str,
    s3_bucket: str,
    db: DuckDBPyConnection,
) -> tuple[dict[str, PartitionedTablePlan], list[DumpTask]]:
    """Plan changed physical partitions for all partitioned tables."""
    plans: dict[str, PartitionedTablePlan] = {}
    tasks: list[DumpTask] = []

    with bigquery_clients() as get_client:
        for table in config.tables:
            if table.strategy != Strategy.PARTITIONED:
                continue

            project = table.name.split(".")[0]
            client = get_client(project)

            plan, table_tasks = await plan_partitioned_table(
                table, client, redis, sync_id, s3_bucket, db
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


async def build_sync_work(
    config: SyncConfig,
    redis: Redis,
    sync_id: str,
    bucket: str,
    db: DuckDBPyConnection,
) -> SyncWork:
    """Build a publisher plan and tasks for changed data only."""
    changed = await detect_changes(config, redis)
    logger.info("Detected %d changed full tables", len(changed))

    changed_tables = [table for table in config.tables if table.name in changed]

    tasks = await expand_config(changed_tables, bucket, sync_id, db)

    tables = {task.table for task in tasks}

    signatures = {
        table: signature for table, signature in changed.items() if table in tables
    }

    paths: dict[str, list[str]] = {}

    for task in tasks:
        paths.setdefault(task.table, []).append(task.bucket_path)

    partitioned, partition_tasks = await plan_partitioned_tables(
        config,
        redis,
        sync_id,
        bucket,
        db,
    )

    tasks.extend(partition_tasks)

    if not signatures and not partitioned:
        logger.info("No changes to plan")
        return SyncWork(plans=[], tasks=[])

    logger.info(
        "Built sync plan with %d full and %d partitioned tables",
        len(signatures),
        len(partitioned),
    )
    return SyncWork(
        plans=group_schema_plans(config, signatures, paths, partitioned), tasks=tasks
    )
