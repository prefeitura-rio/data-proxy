"""Integration steps for PostgreSQL connection lifecycle."""

import asyncio

import psycopg
from psycopg import AsyncConnection
from pytest_bdd import then, when

from tests.fixtures.types import Postgres


@when("I open a PostgreSQL connection", target_fixture="pg_connection")
def open_connection(postgres: Postgres) -> AsyncConnection:
    conn = asyncio.run(psycopg.AsyncConnection.connect(postgres.dsn))
    return conn


@then("the connection is usable")
def connection_usable(pg_connection: AsyncConnection) -> None:
    cursor = asyncio.run(pg_connection.execute(b"SELECT 1"))
    assert asyncio.run(cursor.fetchone()) == (1,)
    asyncio.run(pg_connection.close())
