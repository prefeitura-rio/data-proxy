"""Postgres-backed state for synchronization orchestration.

Cross-run table signatures and partition manifests live in the ``data-proxy.state``
table in the DBOS system database. Run-level state (plans, results, remaining
counts) is owned by DBOS workflows and isn't stored here.
"""

from json import dumps
from typing import cast

from psycopg import AsyncConnection
from psycopg.sql import Identifier

from .executor import execute_sql
from .models import (
    PartitionManifest,
    PublicationResult,
    SyncConfig,
    TableState,
)
from .settings import settings
from .utils import atomic


def schema() -> Identifier:
    """Return the application state schema as a SQL identifier."""
    return Identifier(settings.DBOS_APP_SCHEMA)


async def ensure_app_schema(pg_conn: AsyncConnection) -> None:
    """Create the application state schema and tables when absent."""
    await execute_sql(pg_conn, "postgres/init_schema", mapping={"schema": schema()})
    await pg_conn.commit()


async def emit_error(pg_conn: AsyncConnection, reason: str, **fields: str) -> None:
    """Persist one structured error event in the data-proxy.errors table."""
    await execute_sql(
        pg_conn,
        "postgres/insert_error",
        mapping={"schema": schema()},
        params={"reason": reason, "fields": dumps(fields, default=str)},
    )
    await pg_conn.commit()


async def read_table_state(pg_conn: AsyncConnection, table: str) -> TableState | None:
    """Read committed state for one table."""
    cursor = await execute_sql(
        pg_conn,
        "postgres/read_state",
        mapping={"schema": schema()},
        params={"table_name": table},
    )
    row = cast("tuple[str, ...] | None", await cursor.fetchone())

    if row is None:
        return None

    return TableState.model_validate_json(row[0])


async def read_table_signature(pg_conn: AsyncConnection, table: str) -> str | None:
    """Read the committed signature for one table."""
    state = await read_table_state(pg_conn, table)
    return state.signature if state is not None else None


async def read_partition_manifest(
    pg_conn: AsyncConnection, table: str
) -> PartitionManifest | None:
    """Read a partition manifest from unified table state."""
    state = await read_table_state(pg_conn, table)

    if state is None or state.partitions is None:
        return None

    return PartitionManifest(
        table_signature=state.signature, partitions=state.partitions
    )


async def write_table_state(
    pg_conn: AsyncConnection, table: str, state: TableState
) -> None:
    """Upsert committed state for one table."""
    await execute_sql(
        pg_conn,
        "postgres/upsert_state",
        mapping={"schema": schema()},
        params={"table_name": table, "state": state.model_dump_json()},
    )


async def write_table_states(
    pg_conn: AsyncConnection, states: dict[str, TableState]
) -> None:
    """Commit state for every table atomically."""
    async with atomic(pg_conn):
        for table, state in states.items():
            await write_table_state(pg_conn, table, state)


def build_table_states(
    result: PublicationResult, config: SyncConfig
) -> dict[str, TableState]:
    """Build persisted state for successfully published tables."""
    tables = {table.name: table for table in config.tables}

    states = {
        table_name: TableState(
            strategy=tables[table_name].strategy,
            signature=signature,
            partitions=None,
        )
        for table_name, signature in result.plan.signatures.items()
        if table_name in result.published_tables
    }

    states.update(
        {
            table_name: TableState(
                strategy=tables[table_name].strategy,
                signature=table_plan.table_signature,
                partitions=table_plan.current_partitions,
            )
            for table_name, table_plan in result.plan.partitioned_tables.items()
            if table_name in result.published_tables
        }
    )

    return states
