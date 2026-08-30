"""Change detection and task planning for synchronization runs."""

from dataclasses import dataclass
from hashlib import sha256

from asyncer import asyncify
from google.cloud.bigquery import Client
from loguru import logger
from psycopg.sql import Literal
from redis.asyncio import Redis

from .bigquery import bigquery_clients, physical_partitions, table_modified
from .duckdb import DBConnection
from .models import (
    AllSelection,
    PartitionedTable,
    PartitionedTablePlan,
    PartitionManifest,
    PhysicalPartition,
    SchemaSyncPlan,
    Strategy,
    SyncConfig,
    SyncPlan,
    SyncTask,
    TableConfig,
)
from .state import read_partition_manifest, read_table_signature
from .templates import TemplateSpec, load_template


def discover_json_columns(db: DBConnection, bq_table: str) -> list[str]:
    """Return column names whose DuckDB type contains STRUCT."""
    rows = db.execute(
        load_template(
            TemplateSpec(
                path="duckdb/describe_table",
                mapping={"bq_table": Literal(bq_table)},
            )
        )
    ).fetchall()
    return [str(row[0]) for row in rows if "STRUCT" in str(row[1]).upper()]


def expand_config(
    tables: list[TableConfig],
    gcs_bucket: str,
    sync_id: str,
    db: DBConnection,
) -> list[SyncTask]:
    """Expand full tables into whole-table extraction tasks."""
    tasks: list[SyncTask] = []

    for table in tables:
        if table.strategy != Strategy.FULL:
            continue

        json_columns = discover_json_columns(db, table.name)

        tasks.append(
            table.to_task(
                sync_id,
                gcs_bucket,
                AllSelection(),
                json_columns=json_columns,
            )
        )

    return tasks


def table_signature(table: TableConfig, modified: str) -> str:
    """Combine source modification time with synchronization configuration."""
    config_hash = sha256(table.model_dump_json().encode()).hexdigest()
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
            current = table_signature(table, modified)
            stored = await read_table_signature(redis, table.name)

            if stored != current:
                changed[table.name] = current

    return changed


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
    tasks: list[SyncTask]


def partition_changes(
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
        if full_rebuild or previous.get(partition_id) != partition
    }

    return PartitionChanges(
        full_rebuild=full_rebuild,
        changed=changed,
        removed=previous.keys() - current.keys(),
        previous=previous,
    )


def build_partition_tasks(
    table: PartitionedTable,
    current: dict[str, PhysicalPartition],
    changed: set[str],
    sync_id: str,
    gcs_bucket: str,
    json_columns: list[str],
) -> PartitionTaskBatch:
    """Create one task and path per changed physical partition.

    Numeric partition ids sort first in ascending order; the remainder
    partition (BigQuery's non-numeric ``__NULL__`` id) always sorts last.
    """
    ordered = sorted(
        changed,
        key=lambda partition_id: (
            (1, "") if not partition_id.isdigit() else (0, int(partition_id))
        ),
    )

    tasks = [
        table.to_task(
            sync_id,
            gcs_bucket,
            current[partition_id].selection,
            f"partitions/{partition_id}",
            json_columns,
        )
        for partition_id in ordered
    ]

    paths = {
        partition_id: task.bucket_path
        for partition_id, task in zip(ordered, tasks, strict=True)
    }
    return PartitionTaskBatch(paths=paths, tasks=tasks)


async def plan_partitioned_table(
    table: PartitionedTable,
    client: Client,
    redis: Redis,
    sync_id: str,
    gcs_bucket: str,
    db: DBConnection,
) -> tuple[PartitionedTablePlan | None, list[SyncTask]]:
    """Plan one physically partitioned table."""
    table_sig, current = await asyncify(physical_partitions)(
        client, table.name, table.model_dump_json(), table.n
    )
    stored = await read_partition_manifest(redis, table.name)
    changes = partition_changes(current, stored, table_sig)

    if not changes.changed and not changes.removed:
        return None, []

    json_columns = await asyncify(discover_json_columns)(db, table.name)
    batch = build_partition_tasks(
        table,
        current,
        changes.changed,
        sync_id,
        gcs_bucket,
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
    gcs_bucket: str,
    db: DBConnection,
) -> tuple[dict[str, PartitionedTablePlan], list[SyncTask]]:
    """Plan changed physical partitions for all partitioned tables."""
    plans: dict[str, PartitionedTablePlan] = {}
    tasks: list[SyncTask] = []

    with bigquery_clients() as get_client:
        for table in config.tables:
            if table.strategy != Strategy.PARTITIONED:
                continue

            project = table.name.split(".")[0]
            client = get_client(project)

            plan, table_tasks = await plan_partitioned_table(
                table, client, redis, sync_id, gcs_bucket, db
            )

            if plan is not None:
                plans[table.name] = plan
                tasks.extend(table_tasks)

    return plans, tasks


async def build_sync_plan(
    config: SyncConfig,
    redis: Redis,
    sync_id: str,
    bucket: str,
    db: DBConnection,
) -> tuple[SyncPlan | None, list[SyncTask]]:
    """Build a finalizer plan and tasks for changed data only."""
    changed = await detect_changes(config, redis)
    logger.info("Detected {} changed full tables", len(changed))

    changed_tables = [table for table in config.tables if table.name in changed]

    tasks = expand_config(changed_tables, bucket, sync_id, db)

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
        return None, []

    logger.info(
        "Built sync plan with {} full and {} partitioned tables",
        len(signatures),
        len(partitioned),
    )
    schema_names = {table.name: table.resolved_schema for table in config.tables}
    grouped: dict[str, SchemaSyncPlan] = {}

    for table, signature in signatures.items():
        schema_name = schema_names[table]
        schema_plan = grouped.setdefault(
            schema_name, SchemaSyncPlan(schema_name=schema_name)
        )
        schema_plan.signatures[table] = signature
        schema_plan.paths[table] = paths[table]

    for table, partition_plan in partitioned.items():
        schema_name = schema_names[table]
        grouped.setdefault(
            schema_name, SchemaSyncPlan(schema_name=schema_name)
        ).partitioned_tables[table] = partition_plan

    return SyncPlan(sync_id=sync_id, plans=list(grouped.values())), tasks
