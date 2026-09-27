"""Postgres-backed state for synchronization orchestration.

Cross-run table signatures and partition manifests live in the ``data-proxy.state``
table in the DBOS system database. Run-level state (plans, results, remaining
counts) is owned by DBOS workflows and isn't stored here.
"""

from json import dumps

from psycopg.rows import TupleRow

from .executor import Executor
from .models import PublicationResult, SyncConfig, TableState
from .postgres import Postgres
from .schema import schema
from .types import PostgresParams


async def ensure_app_schema(pg_conn: Postgres) -> None:
    """Create the application state schema and tables when absent."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/init_schema",
        mapping={"schema": schema()},
    )
    await pg_conn.commit()


async def emit_error(pg_conn: Postgres, reason: str, **fields: str) -> None:
    """Persist one structured error event in the data_proxy.errors table."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/insert_error",
        mapping={"schema": schema()},
        params={"reason": reason, "fields": dumps(fields, default=str)},
    )
    await pg_conn.commit()


async def read_table_state(pg_conn: Postgres, table: str) -> TableState | None:
    """Read committed state for one table."""
    rows = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/read_state",
        mapping={"schema": schema()},
        params={"table_name": table},
        expect=tuple[str],
    )

    if not rows:
        return None

    return TableState.model_validate_json(rows[0][0])


async def read_table_signature(pg_conn: Postgres, table: str) -> str | None:
    """Read the committed signature for one table."""
    state = await read_table_state(pg_conn, table)
    return state.signature if state is not None else None


async def write_table_state(pg_conn: Postgres, table: str, state: TableState) -> None:
    """Upsert committed state for one table.

    This does not commit; the caller is responsible for committing the
    transaction, typically through ``write_table_states``.
    """
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/upsert_state",
        mapping={"schema": schema()},
        params={"table_name": table, "state": state.model_dump_json()},
    )


async def write_table_states(pg_conn: Postgres, states: dict[str, TableState]) -> None:
    """Commit state for every table atomically."""
    async with pg_conn.atomic():
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
