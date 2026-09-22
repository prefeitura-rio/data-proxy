"""Integration steps for PostgreSQL schema lifecycle."""

import asyncio
from dataclasses import dataclass
from typing import cast

from psycopg import AsyncConnection, AsyncCursor
from pytest_bdd import given, then, when

from data_proxy.models import FullTable, SchemaConfig, SyncConfig
from data_proxy.schema import initialize_schemas, revoke_anonymous_access
from data_proxy.state import ensure_app_schema
from data_proxy.types import DatabaseRow
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql


@dataclass
class SchemaScenario:
    postgres: Postgres
    other_schema: str | None = None
    memberships_unchanged: bool = False


async def execute_fixture_sql(
    database: Postgres, path: str, mapping: dict[str, str]
) -> AsyncCursor[DatabaseRow]:
    return await execute_sql(database.connection, path, mapping=mapping)


async def fetch_fixture_rows(
    database: Postgres, path: str, mapping: dict[str, str]
) -> list[DatabaseRow]:
    cursor = await execute_fixture_sql(database, path, mapping)
    return await cursor.fetchall()


async def fetch_fixture_one(
    database: Postgres, path: str, mapping: dict[str, str]
) -> DatabaseRow | None:
    cursor = await execute_fixture_sql(database, path, mapping)
    return await cursor.fetchone()


@given("a fresh PostgreSQL schema", target_fixture="schema_context")
def fresh_postgres_schema(postgres: Postgres) -> SchemaScenario:
    return SchemaScenario(postgres=postgres)


@when("I initialize two configured application schemas")
def initialize_two_schemas(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    other = f"{schema}_two"
    config = SyncConfig(
        schemas={
            schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")]),
            other: SchemaConfig(tables=[FullTable(name=f"p.{other}.two")]),
        }
    )
    asyncio.run(initialize_schemas(database.connection, config))
    schema_context.other_schema = other


@when("I initialize one configured application schema")
def initialize_one_schema(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    before = asyncio.run(
        fetch_fixture_rows(
            database, "postgres/role_memberships", {"role": "authenticator"}
        )
    )
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")])}
    )
    asyncio.run(initialize_schemas(database.connection, config))
    after = asyncio.run(
        fetch_fixture_rows(
            database, "postgres/role_memberships", {"role": "authenticator"}
        )
    )
    schema_context.memberships_unchanged = before == after


@when("I revoke anonymous access for the configured schema")
def revoke_schema_access(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")])}
    )
    asyncio.run(
        execute_fixture_sql(
            database,
            "postgres/setup_schema_reload_privileges",
            {"schema": schema},
        )
    )
    asyncio.run(revoke_anonymous_access(database.connection, config))


@when("I create a stale table and clean schema objects")
def clean_schema_objects(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.kept")])}
    )
    asyncio.run(initialize_schemas(database.connection, config))
    asyncio.run(
        database.connection.execute(f'CREATE TABLE "{schema}".stale (id int)'.encode())
    )
    asyncio.run(
        database.connection.execute(
            b"CALL data_proxy.cleanup_stale_objects(%s::jsonb, %s)",
            (config.model_dump_json(), schema),
        )
    )


@then("the maintenance procedures are installed")
def check_maintenance_procedures(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    cursor = asyncio.run(
        database.connection.execute(
            "SELECT to_regprocedure('data_proxy.apply_retention(jsonb,text)')"
        )
    )
    assert asyncio.run(cursor.fetchone()) == ("data_proxy.apply_retention(jsonb,text)",)


@then("both configured schemas exist")
def check_configured_schemas(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    assert schema_context.other_schema is not None
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/schema_names",
            {"schema": schema, "other_schema": schema_context.other_schema},
        )
    )
    assert rows == [(schema,), (schema_context.other_schema,)]


@then("authenticator memberships are unchanged")
def check_memberships(schema_context: SchemaScenario) -> None:
    assert schema_context.memberships_unchanged


@then("anonymous schema usage is disabled")
def check_schema_usage(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    assert asyncio.run(
        fetch_fixture_one(
            database,
            "postgres/has_schema_usage",
            {"schema": database.namespace.schema},
        )
    ) == (False,)


@then("anonymous table access is disabled")
def check_table_access(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    assert asyncio.run(
        fetch_fixture_one(
            database,
            "postgres/has_table_select",
            {"schema": database.namespace.schema},
        )
    ) == (False,)


@then("the stale table does not exist")
def check_stale_table_removed(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regclass(%s)",
            (f"{database.namespace.schema}.stale",),
        )
    )
    assert asyncio.run(cursor.fetchone()) == (None,)


@when("I create a stale fallback view and clean schema objects")
def clean_stale_fallback(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.kept")])}
    )
    asyncio.run(initialize_schemas(database.connection, config))
    asyncio.run(
        database.connection.execute(
            f'CREATE VIEW "{schema}".stale_table_bq AS SELECT 1'.encode()
        )
    )
    asyncio.run(
        database.connection.execute(
            b"CALL data_proxy.cleanup_stale_objects(%s::jsonb, %s)",
            (config.model_dump_json(), schema),
        )
    )


@then("the stale fallback view does not exist")
def check_stale_fallback_removed(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regclass(%s)",
            (f"{schema}.stale_table_bq",),
        )
    )
    assert asyncio.run(cursor.fetchone()) == (None,)


@when("I initialize the application state schema", target_fixture="state_schema_result")
def init_state_schema(schema_context: SchemaScenario) -> dict[str, object]:
    database = schema_context.postgres
    asyncio.run(ensure_app_schema(database.connection))
    return {"schema": database.namespace.schema, "connection": database.connection}


@then("the state table exists")
def check_state_table(state_schema_result: dict[str, object]) -> None:
    conn = cast("AsyncConnection", state_schema_result["connection"])
    cursor = asyncio.run(conn.execute(b"SELECT to_regclass(%s)", ("data_proxy.state",)))
    assert asyncio.run(cursor.fetchone()) is not None


@then("the errors table exists")
def check_errors_table(state_schema_result: dict[str, object]) -> None:
    conn = cast("AsyncConnection", state_schema_result["connection"])
    cursor = asyncio.run(
        conn.execute(b"SELECT to_regclass(%s)", ("data_proxy.errors",))
    )
    assert asyncio.run(cursor.fetchone()) is not None
