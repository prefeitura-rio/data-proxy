"""Change detection and task planning for synchronization runs."""

from hashlib import sha256

import polars as pl
from google.cloud.bigquery import Client
from loguru import logger
from psycopg.sql import Identifier, Literal
from redis.asyncio import Redis

from .bigquery import physical_partitions, table_modified
from .duckdb import DBConnection
from .models import (
    AllSelection,
    AllTable,
    AllWithPartitionsTable,
    PartitionedTablePlan,
    PartitionManifest,
    PhysicalPartition,
    SyncConfig,
    SyncPlan,
    SyncTask,
    TableConfig,
    ValueSelection,
    WindowTable,
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


def discover_partitions(db: DBConnection, table: WindowTable) -> list[str]:
    """Return the last configured window values in descending order."""
    rows = db.execute(
        load_template(
            {
                "path": "duckdb/discover_partitions",
                "mapping": {
                    "bq_table": Literal(table.bq_table),
                    "partition_column": Identifier(table.partition.column),
                },
            }
        )
    ).fetchall()

    if not rows:
        return []

    return (
        pl.DataFrame(rows, orient="row")
        .to_series(0)
        .top_k(table.partition.n)
        .sort(descending=True)
        .cast(pl.String)
        .to_list()
    )


def expand_config(
    config: SyncConfig,
    gcs_bucket: str,
    sync_id: str,
    db: DBConnection,
) -> list[SyncTask]:
    """Expand ordinary all and window tables into extraction tasks."""
    tasks: list[SyncTask] = []
    for table in config.tables:
        json_columns = discover_json_columns(db, table.bq_table)

        match table:
            case AllTable():
                tasks.append(
                    table.to_task(
                        sync_id,
                        gcs_bucket,
                        AllSelection(),
                        json_columns=json_columns,
                    )
                )
            case WindowTable():
                tasks.extend(
                    table.to_task(
                        sync_id,
                        gcs_bucket,
                        ValueSelection(
                            column=table.partition.column,
                            value=partition,
                        ),
                        partition,
                        json_columns,
                    )
                    for partition in discover_partitions(db, table)
                )
            case _:
                pass
    return tasks


def table_signature(table: TableConfig, modified: str) -> str:
    """Combine source modification time with synchronization configuration."""
    config_hash = sha256(table.model_dump_json().encode()).hexdigest()
    return f"{modified}:{config_hash}"


async def detect_changes(config: SyncConfig, redis: Redis) -> dict[str, str]:
    """Return ordinary table signatures changed since their successful sync."""
    clients: dict[str, Client] = {}
    changed: dict[str, str] = {}

    try:
        for table in config.tables:
            if isinstance(table, AllWithPartitionsTable):
                continue

            project = table.bq_table.split(".")[0]
            client = clients.get(project)

            if client is None:
                client = Client(project=project)
                clients[project] = client

            modified = table_modified(client, table.bq_table)
            current = table_signature(table, modified)
            stored = await read_table_signature(redis, table.bq_table)
            if stored != current:
                changed[table.bq_table] = current
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
    table: AllWithPartitionsTable,
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
        partition_id: task.gcs_path
        for partition_id, task in zip(ordered, tasks, strict=True)
    }
    return paths, tasks


async def plan_partitioned_table(
    table: AllWithPartitionsTable,
    client: Client,
    redis: Redis,
    sync_id: str,
    gcs_bucket: str,
    db: DBConnection,
) -> tuple[PartitionedTablePlan | None, list[SyncTask]]:
    """Plan one physically partitioned table."""
    table_sig, current = physical_partitions(
        client, table.bq_table, table.model_dump_json()
    )
    stored = await read_partition_manifest(redis, table.bq_table)
    rebuild, changed, removed, previous = partition_changes(current, stored, table_sig)
    if not changed and not removed:
        return None, []

    paths, tasks = build_partition_tasks(
        table,
        current,
        changed,
        sync_id,
        gcs_bucket,
        discover_json_columns(db, table.bq_table),
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
            if not isinstance(table, AllWithPartitionsTable):
                continue

            project = table.bq_table.split(".")[0]
            client = clients.get(project)
            if client is None:
                client = Client(project=project)
                clients[project] = client

            plan, table_tasks = await plan_partitioned_table(
                table, client, redis, sync_id, gcs_bucket, db
            )
            if plan is not None:
                plans[table.bq_table] = plan
                tasks.extend(table_tasks)
    finally:
        for client in clients.values():
            client.close()

    return plans, tasks


async def plan_sync(
    config: SyncConfig,
    redis: Redis,
    sync_id: str,
    gcs_bucket: str,
    db: DBConnection,
) -> tuple[SyncPlan | None, list[SyncTask]]:
    """Build a finalizer plan and tasks for changed data only."""
    changed = await detect_changes(config, redis)
    logger.info("Detected {} changed ordinary tables", len(changed))

    changed_config = SyncConfig(
        tables=[table for table in config.tables if table.bq_table in changed]
    )
    tasks = expand_config(changed_config, gcs_bucket, sync_id, db)
    task_tables = {task.bq_table for task in tasks}
    signatures = {
        bq_table: signature
        for bq_table, signature in changed.items()
        if bq_table in task_tables
    }
    paths: dict[str, list[str]] = {}
    for task in tasks:
        paths.setdefault(task.bq_table, []).append(task.gcs_path)

    partitioned, partition_tasks = await plan_partitioned_tables(
        config,
        redis,
        sync_id,
        gcs_bucket,
        db,
    )
    tasks.extend(partition_tasks)

    if not signatures and not partitioned:
        logger.info("No changes to plan")
        return None, []

    logger.info(
        "Built sync plan with {} ordinary and {} partitioned tables",
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
