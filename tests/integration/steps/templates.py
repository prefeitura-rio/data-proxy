"""Integration steps for SQL template execution against PostgreSQL.

These steps exercise view reconciliation and S3 cleanup against real
PostgreSQL and Silo instances.
"""

import asyncio
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from data_proxy.models import (
    FullTable,
    SchemaConfig,
    SyncConfig,
    TableConfig,
)
from data_proxy.postgres import Postgres as PgBackend
from data_proxy.s3 import clear_s3_prefix
from data_proxy.schema import initialize_schemas
from data_proxy.settings import settings
from data_proxy.sources import stages
from data_proxy.sources.views import reconcile_views
from tests.fixtures.types import Postgres, Silo
from tests.helpers import fetch_all, function_exists, relation_exists


@dataclass
class TemplateScenario:
    postgres: Postgres


def set_sync_config(
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


def reconcile(
    template_context: TemplateScenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: list[tuple[str, str]],
    fallbacks: list[str],
) -> None:
    """Reconcile one table whose BigQuery source reports the given columns."""
    database = template_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={
            schema: SchemaConfig(
                tables=[FullTable(name=f"p.{schema}.people", fallbacks=fallbacks)]
            )
        }
    )

    async def source_columns(
        pg_conn: PgBackend, table: TableConfig
    ) -> list[tuple[str, str]]:
        return columns

    set_sync_config(monkeypatch, config, tmp_path)
    monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)
    asyncio.run(initialize_schemas(database.backend, config))
    asyncio.run(reconcile_views(database.backend, config))


@when("I reconcile DuckLake views for one table")
def reconcile_ducklake_views(
    template_context: TemplateScenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reconcile DuckLake views for one table with two source columns."""
    reconcile(
        template_context,
        monkeypatch,
        tmp_path,
        [("cpf", "BIGINT"), ("name", "VARCHAR")],
        [],
    )


@when(
    parsers.parse(
        'I reconcile DuckLake views for one table with the columns "{columns}"'
    )
)
def reconcile_with_columns(
    template_context: TemplateScenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: str,
) -> None:
    """Reconcile DuckLake views for one table with the listed source columns."""
    reconcile(
        template_context,
        monkeypatch,
        tmp_path,
        [(name, kind) for name, kind in (c.split(" ") for c in columns.split(", "))],
        [],
    )


@when(
    parsers.parse(
        'I reconcile DuckLake views for one table with the "{fallback}" fallback'
    )
)
def reconcile_with_fallback(
    template_context: TemplateScenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fallback: str,
) -> None:
    """Reconcile DuckLake views for one table that falls back to one source."""
    reconcile(
        template_context,
        monkeypatch,
        tmp_path,
        [("cpf", "BIGINT"), ("name", "VARCHAR")],
        [fallback],
    )


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

    set_sync_config(monkeypatch, config, tmp_path)
    asyncio.run(reconcile_views(database.backend, config))


@then(parsers.re(r'the view "(?P<view>[^"]+)" (?P<state>exists|does not exist)$'))
def check_view(template_context: TemplateScenario, view: str, state: str) -> None:
    database = template_context.postgres
    found = asyncio.run(relation_exists(database, database.namespace.schema, view))
    assert found is (state == "exists")


@then(
    parsers.re(r'the function "(?P<signature>[^"]+)" (?P<state>exists|does not exist)$')
)
def check_function(
    template_context: TemplateScenario, signature: str, state: str
) -> None:
    database = template_context.postgres
    found = asyncio.run(function_exists(database, database.namespace.schema, signature))
    assert found is (state == "exists")


@then(parsers.parse('the application function "{signature}" exists'))
def check_application_function(
    template_context: TemplateScenario, signature: str
) -> None:
    database = template_context.postgres
    assert asyncio.run(function_exists(database, settings.DBOS_APP_SCHEMA, signature))


@then(parsers.parse('the view "{view}" has the columns "{columns}"'))
def check_view_columns(
    template_context: TemplateScenario, view: str, columns: str
) -> None:
    database = template_context.postgres
    rows = asyncio.run(
        fetch_all(
            database,
            "postgres/view_columns",
            params={"schema_name": database.namespace.schema, "table_name": view},
        )
    )
    assert [row[0] for row in rows] == columns.split(", ")


@when("I upload objects to S3 under a test prefix")
def upload_s3_objects(
    template_context: TemplateScenario,
    silo: Silo,
) -> None:
    """Upload two objects to Silo under a test prefix."""
    for name in ("a", "b"):
        data = BytesIO(f"data-{name}".encode())
        silo.client.put_object(
            "test-bucket", f"tmp/test/{name}.parquet", data, data.getbuffer().nbytes
        )


@when("I clear the S3 prefix")
def clear_s3_prefix_step(
    monkeypatch: pytest.MonkeyPatch,
    silo: Silo,
) -> None:
    """Clear the test prefix from S3."""
    monkeypatch.setattr(settings, "S3_ENDPOINT", f"{silo.host}:{silo.port}")
    monkeypatch.setattr(settings, "S3_USE_SSL", False)
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setattr(settings, "S3_SECRET_KEY", "minioadmin")
    asyncio.run(clear_s3_prefix("tmp/test"))


@then("the S3 objects are gone")
def check_s3_objects_gone(silo: Silo) -> None:
    """Verify no objects remain under the test prefix."""
    objects = list(silo.client.list_objects("test-bucket", prefix="tmp/test/"))
    assert objects == []
