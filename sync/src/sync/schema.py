"""The application schema and the PostgREST reload operations."""

from typing import TYPE_CHECKING

from psycopg.rows import TupleRow
from psycopg.sql import Identifier

from .executor import Executor
from .settings import settings
from .types import PostgresParams

if TYPE_CHECKING:
    from .postgres import Postgres


def schema() -> Identifier:
    """Return the application state schema as a SQL identifier."""
    return Identifier(settings.DBOS_APP_SCHEMA)


async def notify_postgrest(pg_conn: Postgres) -> None:
    """Tell PostgREST to reload its schema, after every standby replayed the change."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/notify_postgrest", mapping={"schema": schema()}
    )
    await pg_conn.commit()


async def wait_for_standbys(pg_conn: Postgres) -> None:
    """Wait until every streaming standby replayed what the primary has written."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).execute(
        "postgres/wait_for_standbys", mapping={"schema": schema()}
    )
