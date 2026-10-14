import asyncio
from dataclasses import dataclass

from psycopg.sql import Identifier
from pytest_bdd import given, then, when

from engine.models import FullTable, SchemaConfig, SyncConfig
from engine.settings import settings
from engine.state import ensure_app_schema
from engine.types import DatabaseRow
from tests.fixtures.types import Postgres
from tests.helpers import (
    execute_sql,
    fetch_all,
    fetch_one,
    function_exists,
    initialize_schemas,
    relation_exists,
    revoke_anonymous_access,
)


@dataclass
class SchemaScenario:
    """State shared by the schema steps of one scenario."""

    postgres: Postgres
    other_schema: str | None = None
    memberships_unchanged: bool = False


@given("a fresh PostgreSQL schema", target_fixture="schema_context")
def fresh_postgres_schema(postgres: Postgres) -> SchemaScenario:
    """Fresh postgres schema."""
    return SchemaScenario(postgres=postgres)


@when("I initialize two configured application schemas")
def initialize_two_schemas(schema_context: SchemaScenario) -> None:
    """Initialize two schemas."""
    database = schema_context.postgres
    schema = database.namespace.schema
    other = f"{schema}_two"
    config = SyncConfig(
        schemas={
            schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")]),
            other: SchemaConfig(tables=[FullTable(name=f"p.{other}.two")]),
        }
    )
    asyncio.run(initialize_schemas(database.backend, config))
    schema_context.other_schema = other


@when("I initialize one configured application schema")
def initialize_one_schema(schema_context: SchemaScenario) -> None:
    """Initialize one schema."""
    database = schema_context.postgres
    schema = database.namespace.schema
    before = asyncio.run(authenticator_memberships(database))
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")])}
    )
    asyncio.run(initialize_schemas(database.backend, config))
    after = asyncio.run(authenticator_memberships(database))
    schema_context.memberships_unchanged = before == after


@when("I revoke anonymous access for the configured schema")
def revoke_schema_access(schema_context: SchemaScenario) -> None:
    """Revoke schema access."""
    database = schema_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")])}
    )
    asyncio.run(
        execute_sql(
            database,
            "postgres/setup_schema_reload_privileges",
            mapping={
                "schema": database.namespace.identifier,
                "anonymous_role": Identifier(settings.AUTH_ANON_ROLE),
            },
        )
    )
    asyncio.run(revoke_anonymous_access(database.backend, config))


@when("I create a stale view and clean schema objects")
def clean_stale_view(schema_context: SchemaScenario) -> None:
    """Clean stale view."""
    database = schema_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.kept")])}
    )
    asyncio.run(initialize_schemas(database.backend, config))
    asyncio.run(
        execute_sql(
            database,
            "postgres/create_view",
            mapping={
                "schema": database.namespace.identifier,
                "view": Identifier("stale"),
            },
        )
    )
    asyncio.run(
        execute_sql(
            database,
            "postgres/call_cleanup_stale_objects",
            mapping={"app_schema": Identifier(settings.DBOS_APP_SCHEMA)},
            params={"config": config.model_dump_json(), "schema_name": schema},
        )
    )


@then("the maintenance procedures are installed")
def check_maintenance_procedures(schema_context: SchemaScenario) -> None:
    """Check maintenance procedures."""
    database = schema_context.postgres
    assert [
        asyncio.run(function_exists(database, settings.DBOS_APP_SCHEMA, signature))
        for signature in (
            "cleanup_stale_objects(jsonb, text)",
            "prune_access_log(interval, text)",
        )
    ] == [True, True]


@then("both configured schemas exist")
def check_configured_schemas(schema_context: SchemaScenario) -> None:
    """Check configured schemas."""
    database = schema_context.postgres
    schema = database.namespace.schema
    assert schema_context.other_schema is not None
    rows = asyncio.run(
        fetch_all(
            database,
            "postgres/schema_names",
            params={"schema_names": [schema, schema_context.other_schema]},
        )
    )
    assert rows == [(schema,), (schema_context.other_schema,)]


@then("authenticator memberships are unchanged")
def check_memberships(schema_context: SchemaScenario) -> None:
    """Check memberships."""
    assert schema_context.memberships_unchanged


@then("anonymous schema usage is disabled")
def check_schema_usage(schema_context: SchemaScenario) -> None:
    """Check schema usage."""
    database = schema_context.postgres
    assert asyncio.run(
        fetch_one(
            database,
            "postgres/has_schema_usage",
            params={
                "role_name": settings.AUTH_ANON_ROLE,
                "schema_name": database.namespace.schema,
            },
        )
    ) == (False,)


@then("anonymous table access is disabled")
def check_table_access(schema_context: SchemaScenario) -> None:
    """Check table access."""
    database = schema_context.postgres
    assert asyncio.run(
        fetch_one(
            database,
            "postgres/has_table_select",
            params={
                "role_name": settings.AUTH_ANON_ROLE,
                "table_name": f"{database.namespace.schema}.one",
            },
        )
    ) == (False,)


@then("the stale view does not exist")
def check_stale_view_removed(schema_context: SchemaScenario) -> None:
    """Check stale view removed."""
    database = schema_context.postgres
    assert not asyncio.run(
        relation_exists(database, database.namespace.schema, "stale")
    )


@when("I initialize the application state schema")
def init_state_schema(schema_context: SchemaScenario) -> None:
    """Init state schema."""
    asyncio.run(ensure_app_schema(schema_context.postgres.backend))


@then("the state and errors tables exist")
def check_state_tables(schema_context: SchemaScenario) -> None:
    """Check state tables."""
    database = schema_context.postgres
    assert [
        asyncio.run(relation_exists(database, settings.DBOS_APP_SCHEMA, table))
        for table in ("state", "errors")
    ] == [True, True]


async def authenticator_memberships(database: Postgres) -> list[DatabaseRow]:
    """Return the roles granted to the authenticator role."""
    return await fetch_all(
        database,
        "postgres/role_memberships",
        params={"role_name": settings.AUTH_AUTHENTICATOR_ROLE},
    )
