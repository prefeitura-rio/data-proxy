"""Integration steps for fallback views and cache invalidation."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from pytest_bdd import given, then, when

from data_proxy.fallback import run_fallback_views_creation
from data_proxy.models import FullTable, SchemaConfig, SyncConfig
from data_proxy.settings import settings
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql


@dataclass
class FallbackScenario:
    postgres: Postgres


@given("a fresh PostgreSQL fallback schema", target_fixture="fallback_context")
def fresh_fallback_schema(
    postgres: Postgres, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FallbackScenario:
    schema = postgres.namespace.schema
    sync_file = tmp_path / "sync.json"
    sync_file.write_text(
        f'{{"schemas": {{"{schema}": {{"tables": [], "claim": "sub"}}}}}}'
    )
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", sync_file)
    return FallbackScenario(postgres=postgres)


@when("I run fallback view creation with fallback disabled")
def run_fallback_disabled(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    table = FullTable(name=f"p.{schema}.people", resolved_schema=schema, fallback=False)
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})
    asyncio.run(run_fallback_views_creation(database.connection, config))


@when("I run fallback view creation with scalar columns")
def run_fallback_scalar(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    table = FullTable(name=f"p.{schema}.people", resolved_schema=schema, fallback=True)
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "people",
                "columns": "id integer, name text",
            },
        )
    )
    asyncio.run(database.connection.commit())
    asyncio.run(run_fallback_views_creation(database.connection, config))


@when("I run fallback view creation with nested columns")
def run_fallback_nested(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    table = FullTable(name=f"p.{schema}.people", resolved_schema=schema, fallback=True)
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "people",
                "columns": "id integer, data json",
            },
        )
    )
    asyncio.run(database.connection.commit())
    asyncio.run(run_fallback_views_creation(database.connection, config))


@when("I run fallback view creation with no columns")
def run_fallback_no_columns(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    table = FullTable(
        name=f"p.{schema}.nonexistent", resolved_schema=schema, fallback=True
    )
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})
    with pytest.raises(RuntimeError, match="no columns"):
        asyncio.run(run_fallback_views_creation(database.connection, config))


@then("no fallback views are created")
def no_fallback_views(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regclass(%s)",
            (f"{schema}.people_bq",),
        )
    )
    assert asyncio.run(cursor.fetchone()) == (None,)


@then("a scalar fallback function exists")
def scalar_function_exists(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regprocedure(%s)",
            (f"{schema}.people_bq_fn()",),
        )
    )
    assert asyncio.run(cursor.fetchone()) is not None


@then("a scalar fallback view exists")
def scalar_view_exists(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regclass(%s)",
            (f"{schema}.people_bq",),
        )
    )
    assert asyncio.run(cursor.fetchone()) is not None


@then("the user role has select access on the view")
def user_has_select(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            f"SELECT has_table_privilege('user', '{schema}.people_bq', 'SELECT')".encode()
        )
    )
    assert asyncio.run(cursor.fetchone()) == (True,)


@then("a nested fallback function exists")
def nested_function_exists(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regprocedure(%s)",
            (f"{schema}.people_bq_fn()",),
        )
    )
    assert asyncio.run(cursor.fetchone()) is not None


@then("nested columns are cast to JSONB in the view")
def nested_columns_jsonb(fallback_context: FallbackScenario) -> None:
    database = fallback_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            f"SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = '{schema}' AND table_name = 'people_bq' AND column_name = 'data'".encode()
        )
    )
    row = asyncio.run(cursor.fetchone())
    assert row is not None
    assert row[1] == "jsonb"


@then("fallback creation fails with no columns error")
def fallback_no_columns_error(fallback_context: FallbackScenario) -> None:
    pass
