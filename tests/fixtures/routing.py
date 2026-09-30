"""Routing function fixtures."""

import pytest
from psycopg.rows import TupleRow
from psycopg.sql import Identifier

from data_proxy.executor import Executor
from data_proxy.settings import settings
from data_proxy.types import PostgresParams
from tests.fixtures.types import Postgres
from tests.helpers import create_access_policy, execute_sql


@pytest.fixture
async def routing(postgres: Postgres) -> Postgres:
    """Install the routing functions in the test transaction."""
    await Executor[PostgresParams, list[TupleRow]](conn=postgres.backend).execute(
        "postgres/sources/routing/snapshot",
        mapping={"schema": Identifier(settings.DBOS_APP_SCHEMA)},
    )
    for template in ("coverage", "plan", "response"):
        await Executor[PostgresParams, list[TupleRow]](conn=postgres.backend).execute(
            f"postgres/sources/routing/{template}",
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
