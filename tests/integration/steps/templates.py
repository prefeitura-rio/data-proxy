"""Integration steps for SQL template execution against PostgreSQL.

These steps exercise Postgres SQL templates through the application's
high-level functions (ensure_app_schema, apply_table_authorization) against
a real PostgreSQL instance. Step phrases are unique to avoid collisions
with the schema and authorization step modules.
"""

import asyncio
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TypedDict

import pytest
from psycopg import AsyncConnection
from pytest_bdd import given, then, when

from data_proxy.authorization import apply_table_authorization
from data_proxy.fallback import reconcile_views
from data_proxy.models import FullTable, SchemaConfig, SyncConfig, UnitMapping
from data_proxy.s3 import clear_s3_prefix
from data_proxy.schema import initialize_schemas
from data_proxy.settings import settings
from data_proxy.state import ensure_app_schema
from data_proxy.types import DatabaseRow
from tests.fixtures.types import Postgres, SeaweedFS
from tests.helpers import execute_sql


class StateSchemaResult(TypedDict):
    """Schema fixture data shared by the template scenarios."""

    schema: str
    connection: AsyncConnection


@dataclass
class TemplateScenario:
    postgres: Postgres


async def fetch_fixture_rows(
    database: Postgres, path: str, mapping: dict[str, str]
) -> list[DatabaseRow]:
    """Execute a SQL fixture template and return every row."""
    cursor = await execute_sql(database.connection, path, mapping=mapping)
    return await cursor.fetchall()


def _set_sync_config(
    monkeypatch: pytest.MonkeyPatch, config: SyncConfig, tmp_path: Path
) -> None:
    """Point settings at one config file and invalidate the cached property."""
    config_path = tmp_path / "sync.json"
    config_path.write_text(config.model_dump_json())
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", config_path)
    monkeypatch.delattr(settings, "sync_config", raising=False)


@given("a fresh PostgreSQL schema for templates", target_fixture="template_context")
def fresh_postgres_schema(postgres: Postgres) -> TemplateScenario:
    """Provide one isolated PostgreSQL schema for template execution."""
    return TemplateScenario(postgres=postgres)


@given(
    "a fresh PostgreSQL authorization schema for templates",
    target_fixture="template_context",
)
def fresh_authorization_schema(postgres: Postgres) -> TemplateScenario:
    """Provide one isolated PostgreSQL authorization schema."""
    return TemplateScenario(postgres=postgres)


@when("I execute the init_schema template", target_fixture="template_state_result")
def execute_init_schema(template_context: TemplateScenario) -> StateSchemaResult:
    """Execute the init_schema template through ensure_app_schema."""
    database = template_context.postgres
    asyncio.run(ensure_app_schema(database.backend))
    return {"schema": database.namespace.schema, "connection": database.connection}


@when("I apply authorization to an unprotected table through templates")
def apply_unprotected_through_templates(template_context: TemplateScenario) -> None:
    """Apply authorization to an unprotected table through templates."""
    database = template_context.postgres
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_table",
            mapping={
                "schema": database.namespace.schema,
                "table": "table",
                "columns": "id_cras text",
            },
        )
    )
    asyncio.run(
        apply_table_authorization(
            database.backend,
            database.namespace.schema,
            "table",
            None,
            None,
        )
    )


@when("I apply authorization to a protected table through templates")
def apply_protected_through_templates(template_context: TemplateScenario) -> None:
    """Apply authorization to a protected table through templates."""
    database = template_context.postgres
    mapping = {"schema": database.namespace.schema}
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_table",
            mapping={
                **mapping,
                "table": "table",
                "columns": "id_cras text",
            },
        )
    )
    asyncio.run(
        execute_sql(
            database.connection, "postgres/create_access_policy", mapping=mapping
        )
    )
    asyncio.run(
        apply_table_authorization(
            database.backend,
            database.namespace.schema,
            "table",
            [UnitMapping(column="id_cras", unit_type="cras")],
            "preferred_username",
        )
    )


@then("the state table exists from the template")
def check_state_table(template_state_result: StateSchemaResult) -> None:
    """Verify the state table exists."""
    conn = template_state_result["connection"]
    cursor = asyncio.run(conn.execute(b"SELECT to_regclass(%s)", ("data_proxy.state",)))
    assert asyncio.run(cursor.fetchone()) is not None


@then("the errors table exists from the template")
def check_errors_table(template_state_result: StateSchemaResult) -> None:
    """Verify the errors table exists."""
    conn = template_state_result["connection"]
    cursor = asyncio.run(
        conn.execute(b"SELECT to_regclass(%s)", ("data_proxy.errors",))
    )
    assert asyncio.run(cursor.fetchone()) is not None


@then("the table has the schema-scoped policy from the template")
def check_schema_scoped_policy(template_context: TemplateScenario) -> None:
    """Verify the table has the schema-scoped policy."""
    database = template_context.postgres
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/policy_names",
            {
                "schema": database.namespace.schema,
                "table": "table",
            },
        )
    )
    assert rows == [("schema_scoped",)]


@then("the user role has select access from the template")
def check_user_select_access(template_context: TemplateScenario) -> None:
    """Verify the user role has select access."""
    database = template_context.postgres
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/select_grants",
            {
                "schema": database.namespace.schema,
                "table": "table",
            },
        )
    )
    assert rows == [("user",)]


@then("the table has the access-policy policy from the template")
def check_access_policy(template_context: TemplateScenario) -> None:
    """Verify the table has the access-policy policy."""
    database = template_context.postgres
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/policy_names",
            {
                "schema": database.namespace.schema,
                "table": "table",
            },
        )
    )
    assert rows == [("access_policy_scoped",)]


@when("I reconcile DuckLake views for one table")
def reconcile_ducklake_views(
    template_context: TemplateScenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reconcile DuckLake views for one table with mocked column types."""
    import data_proxy.fallback as fallback

    database = template_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.people")])}
    )

    _set_sync_config(monkeypatch, config, tmp_path)
    asyncio.run(initialize_schemas(database.backend, config))

    async def mock_column_types(
        pg_conn: object, table: object
    ) -> list[tuple[str, str]]:
        return [("cpf", "BIGINT"), ("name", "VARCHAR")]

    monkeypatch.setattr(fallback, "column_types_from_duckdb", mock_column_types)
    asyncio.run(reconcile_views(database.backend, config))


@then("the DuckLake view exists")
def check_ducklake_view(template_context: TemplateScenario) -> None:
    database = template_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regclass(%s)",
            (f"{schema}.people",),
        )
    )
    assert asyncio.run(cursor.fetchone()) is not None


@then("the DuckLake query function exists")
def check_ducklake_function(template_context: TemplateScenario) -> None:
    database = template_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regprocedure(%s)",
            (f"{schema}.people_fn()",),
        )
    )
    assert asyncio.run(cursor.fetchone()) is not None


@then("the change-feed function exists")
def check_change_feed_function(template_context: TemplateScenario) -> None:
    database = template_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regprocedure(%s)",
            (f"{schema}.ducklake_changes_people(bigint, bigint)",),
        )
    )
    assert asyncio.run(cursor.fetchone()) is not None


@then("the snapshot function exists")
def check_snapshot_function(template_context: TemplateScenario) -> None:
    database = template_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regprocedure(%s)",
            (f"{schema}.ducklake_latest_snapshot()",),
        )
    )
    assert asyncio.run(cursor.fetchone()) is not None


@when("I reconcile DuckLake views with an empty config")
def reconcile_empty_config(
    template_context: TemplateScenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reconcile with an empty config to trigger drop of stale views."""
    database = template_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[])})

    _set_sync_config(monkeypatch, config, tmp_path)
    asyncio.run(reconcile_views(database.backend, config))


@then("the DuckLake view does not exist")
def check_ducklake_view_gone(template_context: TemplateScenario) -> None:
    database = template_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regclass(%s)",
            (f"{schema}.people",),
        )
    )
    assert asyncio.run(cursor.fetchone()) == (None,)


@then("the DuckLake query function does not exist")
def check_ducklake_function_gone(template_context: TemplateScenario) -> None:
    database = template_context.postgres
    schema = database.namespace.schema
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regprocedure(%s)",
            (f"{schema}.people_fn()",),
        )
    )
    assert asyncio.run(cursor.fetchone()) == (None,)


@when("I upload objects to S3 under a test prefix")
def upload_s3_objects(
    template_context: TemplateScenario,
    seaweedfs: SeaweedFS,
) -> None:
    """Upload two objects to SeaweedFS under a test prefix."""
    for name in ("a", "b"):
        data = BytesIO(f"data-{name}".encode())
        seaweedfs.client.put_object(
            "test-bucket", f"tmp/test/{name}.parquet", data, data.getbuffer().nbytes
        )


@when("I clear the S3 prefix")
def clear_s3_prefix_step(
    monkeypatch: pytest.MonkeyPatch,
    seaweedfs: SeaweedFS,
) -> None:
    """Clear the test prefix from S3."""
    monkeypatch.setattr(settings, "S3_ENDPOINT", f"{seaweedfs.host}:{seaweedfs.port}")
    monkeypatch.setattr(settings, "S3_USE_SSL", False)
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setattr(settings, "S3_SECRET_KEY", "minioadmin")
    asyncio.run(clear_s3_prefix("tmp/test"))


@then("the S3 objects are gone")
def check_s3_objects_gone(seaweedfs: SeaweedFS) -> None:
    """Verify no objects remain under the test prefix."""
    objects = list(seaweedfs.client.list_objects("test-bucket", prefix="tmp/test/"))
    assert objects == []
