"""Change detection and task planning for synchronization runs."""

from hashlib import sha256

from google.cloud.bigquery import Client
from loguru import logger
from psycopg.sql import Literal
from redis.asyncio import Redis

from .bigquery import physical_partitions, table_modified
from .duckdb import DBConnection
from .models import (
    AllSelection,
    PartitionedTable,
    PartitionedTablePlan,
    PartitionManifest,
    PhysicalPartition,
    Strategy,
    SyncConfig,
    SyncPlan,
    SyncTask,
    TableConfig,
)
from .state import read_partition_manifest, read_table_signature
from .templates import load_template


def discover_json_columns(db: DBConnection, bq_table: str) -> list[str]:
    """Return column names whose DuckDB type contains STRUCT."""
    rows = db.execute(
        load_template(
            {
                "path": "duckdb/describe_table",
                "mapping": {"bq_table": Literal(bq_table)},
            }
        )
    ).fetchall()
    return [str(row[0]) for row in rows if "STRUCT" in str(row[1]).upper()]


def expand_config(
    config: SyncConfig,
    gcs_bucket: str,
    sync_id: str,
    db: DBConnection,
) -> list[SyncTask]:
    """Expand full tables into whole-table extraction tasks."""
    tasks: list[SyncTask] = []

    for table in config.tables:
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
    clients: dict[str, Client] = {}
    changed: dict[str, str] = {}

    try:
        for table in config.tables:
            if table.strategy != Strategy.FULL:
                continue

            project = table.name.split(".")[0]
            client = clients.get(project)
            if client is None:
                client = Client(project=project)
                clients[project] = client
            modified = table_modified(client, table.name)
            current = table_signature(table, modified)
            stored = await read_table_signature(redis, table.name)

            if stored != current:
                changed[table.name] = current
    finally:
        for client in clients.values():
            client.close()

    return changed


def partition_changes(
    current: dict[str, PhysicalPartition],
    stored: PartitionManifest | None,
    table_signature: str,
) -> tuple[bool, set[str], set[str], dict[str, PhysicalPartition]]:
    """Return rebuild status and changed, removed, and previous partitions."""
    first_sync = stored is None
    table_changed = stored is not None and stored.table_signature != table_signature
    full_rebuild = first_sync or table_changed
    previous = stored.partitions if stored is not None else {}
    changed = {
        partition_id
        for partition_id, partition in current.items()
        if full_rebuild or previous.get(partition_id) != partition
    }
    return full_rebuild, changed, set(previous) - set(current), previous


def build_partition_tasks(
    table: PartitionedTable,
    current: dict[str, PhysicalPartition],
    changed: set[str],
    sync_id: str,
    gcs_bucket: str,
    json_columns: list[str],
) -> tuple[dict[str, str], list[SyncTask]]:
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
            current[partition_id].to_selection(),
            f"partitions/{partition_id}",
            json_columns,
        )
        for partition_id in ordered
    ]

    paths = {
        partition_id: task.bucket_path
        for partition_id, task in zip(ordered, tasks, strict=True)
    }
    return paths, tasks


async def plan_partitioned_table(
    table: PartitionedTable,
    client: Client,
    redis: Redis,
    sync_id: str,
    gcs_bucket: str,
    db: DBConnection,
) -> tuple[PartitionedTablePlan | None, list[SyncTask]]:
    """Plan one physically partitioned table."""
    table_sig, current = physical_partitions(
        client, table.name, table.model_dump_json(), table.n
    )
    stored = await read_partition_manifest(redis, table.name)
    rebuild, changed, removed, previous = partition_changes(current, stored, table_sig)
    if not changed and not removed:
        return None, []

    paths, tasks = build_partition_tasks(
        table,
        current,
        changed,
        sync_id,
        gcs_bucket,
        discover_json_columns(db, table.name),
    )

    plan = PartitionedTablePlan(
        table_signature=table_sig,
        full_rebuild=rebuild,
        current_partitions=current,
        changed_paths=paths,
        removed_partitions={
            partition_id: previous[partition_id] for partition_id in removed
        },
    )

    return plan, tasks


async def plan_partitioned_tables(
    config: SyncConfig,
    redis: Redis,
    sync_id: str,
    gcs_bucket: str,
    db: DBConnection,
) -> tuple[dict[str, PartitionedTablePlan], list[SyncTask]]:
    """Plan changed physical partitions for all partitioned tables."""
    clients: dict[str, Client] = {}
    plans: dict[str, PartitionedTablePlan] = {}
    tasks: list[SyncTask] = []

    try:
        for table in config.tables:
            if table.strategy != Strategy.PARTITIONED:
                continue

            project = table.name.split(".")[0]
            client = clients.get(project)

            if client is None:
                client = Client(project=project)
                clients[project] = client

            plan, table_tasks = await plan_partitioned_table(
                table, client, redis, sync_id, gcs_bucket, db
            )

            if plan is not None:
                plans[table.name] = plan
                tasks.extend(table_tasks)
    finally:
        for client in clients.values():
            client.close()

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

    diff = SyncConfig(
        tables=[table for table in config.tables if table.name in changed]
    )

    tasks = expand_config(diff, bucket, sync_id, db)

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
    return (
        SyncPlan(
            sync_id=sync_id,
            signatures=signatures,
            paths=paths,
            partitioned_tables=partitioned,
        ),
        tasks,
    )
