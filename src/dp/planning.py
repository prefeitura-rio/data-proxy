"""Change detection and task planning for synchronization runs."""

from hashlib import sha256

import polars as pl
from google.cloud.bigquery import Client
from loguru import logger
from redis.asyncio import Redis

from .bigquery import table_modified
from .duckdb import DBConnection
from .models import (
    DumpTable,
    SyncConfig,
    SyncPlan,
    SyncTask,
    TableConfig,
    WindowTable,
)
from .state import read_table_signature
from .templates import load_template


def discover_json_columns(db: DBConnection, bq_table: str) -> list[str]:
    """Return column names whose DuckDB type contains STRUCT."""
    rows = db.execute(f"DESCRIBE SELECT * FROM bigquery_scan('{bq_table}')").fetchall()
    return [str(row[0]) for row in rows if "STRUCT" in str(row[1]).upper()]


def discover_partitions(db: DBConnection, table: WindowTable) -> list[str]:
    """Return the last configured window values in descending order."""
    rows = db.execute(
        load_template(
            "duckdb/discover_partitions",
            {
                "bq_table": table.bq_table,
                "partition_column": table.partition.column,
            },
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
    """Expand dump and window tables into extraction tasks."""
    tasks: list[SyncTask] = []
    for table in config.tables:
        json_columns = discover_json_columns(db, table.bq_table)

        match table:
            case DumpTable():
                tasks.append(
                    table.to_task(sync_id, gcs_bucket, json_columns=json_columns)
                )
            case WindowTable():
                tasks.extend(
                    table.to_task(
                        sync_id,
                        gcs_bucket,
                        partition,
                        table.partition.column,
                        json_columns=json_columns,
                    )
                    for partition in discover_partitions(db, table)
                )
    return tasks


def table_signature(table: TableConfig, modified: str) -> str:
    """Combine source modification time with synchronization configuration."""
    config_hash = sha256(table.model_dump_json().encode()).hexdigest()
    return f"{modified}:{config_hash}"


async def detect_changes(config: SyncConfig, redis: Redis) -> dict[str, str]:
    """Return signatures for tables changed since their successful sync."""
    clients: dict[str, Client] = {}
    changed: dict[str, str] = {}

    try:
        for table in config.tables:
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


async def plan_sync(
    config: SyncConfig,
    redis: Redis,
    sync_id: str,
    gcs_bucket: str,
    db: DBConnection,
) -> tuple[SyncPlan | None, list[SyncTask]]:
    """Build a finalizer plan and tasks for changed tables only."""
    changed = await detect_changes(config, redis)
    logger.info("Detected {} changed tables", len(changed))

    if not changed:
        logger.info("No changes to plan")
        return None, []

    changed_config = SyncConfig(
        tables=[table for table in config.tables if table.bq_table in changed]
    )

    tasks = expand_config(changed_config, gcs_bucket, sync_id, db)
    logger.info("Expanded {} extraction tasks", len(tasks))

    task_tables = {task.bq_table for task in tasks}

    signatures = {
        bq_table: signature
        for bq_table, signature in changed.items()
        if bq_table in task_tables
    }

    if not tasks:
        logger.info("No extraction tasks generated")
        return None, []

    paths: dict[str, list[str]] = {}

    for task in tasks:
        paths.setdefault(task.bq_table, []).append(task.gcs_path)

    logger.info("Built sync plan with {} tables", len(signatures))
    return SyncPlan(sync_id=sync_id, signatures=signatures, paths=paths), tasks
