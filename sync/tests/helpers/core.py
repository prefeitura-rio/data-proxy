"""Run SQL and read settings in the test transaction, and share the helpers of the worker."""

import fcntl
import json
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import LiteralString

import psycopg
import pytest
from psycopg import AsyncConnection, AsyncCursor
from psycopg.sql import SQL, Identifier

from sync.templates import render_template
from sync.types import (
    DatabaseRow,
    DatabaseValue,
    TemplateValue,
)
from tests.constants import (
    HELM_SQL,
    POSTGRES_URL,
    TEST_SQL_DIR,
)
from tests.fixtures.types import Postgres

type SqlParams = Mapping[str, DatabaseValue | list[str]]


async def execute_sql(
    pg: Postgres,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: SqlParams | None = None,
) -> AsyncCursor[DatabaseRow]:
    """Execute one SQL test template in the current transaction."""
    return await pg.connection.execute(
        render_template(path, {} if mapping is None else mapping, root=TEST_SQL_DIR),
        params,
    )


async def fetch_all(
    pg: Postgres,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: SqlParams | None = None,
) -> list[DatabaseRow]:
    """Execute one SQL test template and return every row."""
    cursor = await execute_sql(pg, path, mapping=mapping, params=params)
    return await cursor.fetchall()


async def fetch_one(
    pg: Postgres,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: SqlParams | None = None,
) -> DatabaseRow | None:
    """Execute one SQL test template and return its first row."""
    cursor = await execute_sql(pg, path, mapping=mapping, params=params)
    return await cursor.fetchone()


async def scalar(
    pg: Postgres, sql: str, *params: DatabaseValue | list[str]
) -> DatabaseValue:
    """Run one short inline statement and return its first value."""
    cursor = await pg.connection.execute(sql.encode(), params)
    row = await cursor.fetchone()
    assert row is not None
    value: DatabaseValue = row[0]
    return value


async def set_setting(pg: Postgres, name: str, value: str) -> None:
    """Set one session setting, as PostgREST and data_proxy.pre_request do per request."""
    await pg.connection.execute("SELECT set_config(%s, %s, false)", (name, value))


async def set_request_headers(pg: Postgres, headers: dict[str, str]) -> None:
    """Set the request headers as PostgREST does."""
    await set_setting(pg, "request.headers", json.dumps(headers))


async def response_headers(pg: Postgres) -> dict[str, str]:
    """Return the response headers as one mapping.

    PostgREST reads response.headers as a JSON list of one-entry objects.
    """
    raw = await scalar(pg, "SELECT current_setting('response.headers', true)")
    text = "" if raw is None else str(raw)
    sent: list[dict[str, str]] = json.loads(text) if len(text) > 0 else []
    return {name: value for header in sent for name, value in header.items()}


async def relation_exists(pg: Postgres, schema: str, relation: str) -> bool:
    """Return whether one table or view exists in a schema."""
    row = await fetch_one(
        pg,
        "postgres/relation_exists",
        params={"relation": Identifier(schema, relation).as_string(None)},
    )
    return row == (True,)


async def function_exists(pg: Postgres, schema: str, signature: str) -> bool:
    """Return whether one function signature exists in a schema."""
    row = await fetch_one(
        pg,
        "postgres/function_exists",
        params={"signature": f"{Identifier(schema).as_string(None)}.{signature}"},
    )
    return row == (True,)


def psql_script(*statements: str) -> str:
    """Join rendered statements into one psql script."""
    return ";\n".join(statements) + ";\n"


def helm_sql(name: str, mapping: Mapping[str, TemplateValue] | None = None) -> str:
    """Render one Helm SQL template as the init and maintenance jobs do."""
    return render_template(name, {} if mapping is None else mapping, root=HELM_SQL)


async def rows(connection: AsyncConnection, sql: str) -> list[tuple[object, ...]]:
    """Return every row of one inline query."""
    cursor = await connection.execute(sql.encode())
    return await cursor.fetchall()


async def query_plan(pg: Postgres, query: str) -> str:
    """Return the verbose plan of a query as one text."""
    cursor = await pg.connection.execute(
        f"EXPLAIN (VERBOSE, COSTS OFF) {query}".encode()
    )
    return "\n".join(str(row[0]) for row in await cursor.fetchall())


async def values(pg: Postgres, sql: str, *params: object) -> list[object]:
    """Return the first column of every row of a statement."""
    cursor = await pg.connection.execute(sql.encode(), params)

    return [row[0] for row in await cursor.fetchall()]


async def names(pg: Postgres, sql: str) -> list[object]:
    """Return the first column of every row of a statement."""
    cursor = await pg.connection.execute(sql.encode())

    return [row[0] for row in await cursor.fetchall()]


@contextmanager
def one_worker_at_a_time(factory: pytest.TempPathFactory) -> Generator[None]:
    """Hold a lock that every xdist worker shares, so a setup that all of them run does not overlap."""
    lock = factory.getbasetemp().parent / "setup.lock"
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def clone_database(
    factory: pytest.TempPathFactory,
    source: str,
    name: str,
    settings: tuple[LiteralString, ...] = (),
) -> str:
    """Create the database name from the template source, for one xdist worker, and give it the settings."""
    with (
        one_worker_at_a_time(factory),
        psycopg.connect(f"{POSTGRES_URL}/postgres", autocommit=True) as admin,
    ):
        identifier = Identifier(name)
        admin.execute(SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(identifier))
        admin.execute(
            SQL("CREATE DATABASE {} TEMPLATE {}").format(identifier, Identifier(source))
        )
        for setting in settings:
            admin.execute(
                SQL("ALTER DATABASE {} SET {} = 'on'").format(identifier, SQL(setting))
            )

    return name
