"""Fixtures that install the routing functions and the rbac schema in the test transaction."""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import TupleRow
from psycopg.sql import Identifier

from sync.executor import Executor
from sync.settings import settings
from sync.types import PostgresParams
from tests.constants import FULL, POSTGRES_URL, ROUTED_TABLE
from tests.fixtures.types import Postgres
from tests.helpers import (
    create_access_policy,
    execute_sql,
    install_aggregate_function,
    install_rbac,
    put_state,
)


@pytest.fixture
async def routing(postgres: Postgres) -> Postgres:
    """Install the routing functions in the test transaction."""
    await Executor[PostgresParams, list[TupleRow]](conn=postgres.backend).execute(
        "postgres/views/routing/snapshot",
        mapping={"schema": Identifier(settings.DBOS_APP_SCHEMA)},
    )
    for template in ("coverage", "filter", "plan", "response"):
        await Executor[PostgresParams, list[TupleRow]](conn=postgres.backend).execute(
            f"postgres/views/routing/{template}",
            mapping={"schema": Identifier(settings.DBOS_APP_SCHEMA)},
        )
    return postgres


@pytest.fixture
async def table_function(routing: Postgres) -> Postgres:
    """Install helpers that report the arguments the per-table function passes."""
    await create_access_policy(routing)
    await execute_sql(
        routing,
        "postgres/create_routing_stubs",
        mapping={"schema": routing.namespace.identifier},
    )
    return routing


@pytest.fixture
async def aggregate_function(routing: Postgres) -> Postgres:
    """Install a DuckLake helper with six rows, for the aggregate function."""
    await create_access_policy(routing)
    await execute_sql(
        routing,
        "postgres/create_aggregate_stubs",
        mapping={"schema": routing.namespace.identifier},
    )
    return routing


@pytest.fixture
async def rbac(postgres: Postgres) -> Postgres:
    """Install the rbac schema from the real rules file in the test transaction."""
    await install_rbac(postgres)
    return postgres


@pytest.fixture
def rbac_connection(database: str) -> Iterator[psycopg.Connection[TupleRow]]:
    """Give a property test one synchronous connection to the test database."""
    with psycopg.connect(f"{POSTGRES_URL}/{database}") as connection:
        yield connection
        connection.rollback()


@pytest.fixture
async def installed(aggregate_function: Postgres) -> Postgres:
    """Install the aggregate function for a table without row rules, and mark DuckLake as complete."""
    await install_aggregate_function(aggregate_function, has_rls=False, fallbacks=[])
    await put_state(aggregate_function, ROUTED_TABLE, FULL)
    return aggregate_function


@pytest.fixture
async def capped(aggregate_function: Postgres) -> Postgres:
    """Install the aggregate function with a cap of two rows, over a table that has five labels."""
    await install_aggregate_function(
        aggregate_function, has_rls=False, fallbacks=[], max_rows=2
    )
    await put_state(aggregate_function, ROUTED_TABLE, FULL)
    return aggregate_function
