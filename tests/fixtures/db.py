"""Database and model fixtures."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit

import duckdb
import psycopg
import pytest
from dbos._migration import ensure_dbos_schema, run_dbos_migrations
from psycopg.sql import SQL, Identifier, Literal
from sqlalchemy import create_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.network import Network

from data_proxy.catalog import CatalogPaths
from data_proxy.conditions import schema_scope_condition
from data_proxy.duckdb import DuckDB
from data_proxy.models import (
    DumpTask,
    FullTable,
    SchemaConfig,
    Strategy,
    SyncConfig,
    TableConfig,
    TableState,
    UnitMapping,
)
from data_proxy.models import (
    Table as TableModel,
)
from data_proxy.postgres import Postgres as Pg
from data_proxy.settings import settings
from data_proxy.sources.partitions import PartitionKindConfig, TaskSelection
from data_proxy.state import ensure_app_schema, write_table_state
from data_proxy.templates import render_template
from data_proxy.views import stages
from data_proxy.views.reconcile import reconcile_views
from tests.constants import FILES, TEST_SQL_DIR
from tests.fixtures.types import (
    Postgres,
    PostgresTestNamespace,
    Psql,
    Silo,
)
from tests.helpers import helm_sql, initialize_schemas, psql_script

PG_DUCKDB_SETTINGS: Final = {
    "duckdb.unsafe_allow_execution_inside_functions": "on",
    "duckdb.unsafe_allow_mixed_transactions": "on",
}


@pytest.fixture
def invalid_rls() -> list[UnitMapping]:
    """Return an invalid runtime RLS value for guard tests."""
    rls = TableModel.model_construct(rls="invalid").rls
    if rls is None:
        raise AssertionError("invalid RLS fixture must contain a value")
    return rls


@pytest.fixture
def invalid_kind_config() -> PartitionKindConfig:
    """Return an invalid partition kind config for guard tests."""
    return cast("PartitionKindConfig", cast("object", SimpleNamespace(kind="invalid")))


@pytest.fixture
def invalid_selection() -> TaskSelection:
    """Return an unknown task selection for guard tests."""
    task = DumpTask.model_construct(
        run_id="r",
        table="p.d.t",
        target_schema="d",
        bucket_path="s3://b/t.parquet",
        selections=[object()],
    )
    return task.selections[0]


@pytest.fixture(scope="session")
def postgres_container(container_network: Network) -> Iterator[PostgresContainer]:
    """Provide the real PostgreSQL and pg_duckdb integration boundary."""
    files_dir = str((Path(__file__).parent.parent / "files").absolute())

    container = PostgresContainer(
        "ghcr.io/prefeitura-rio/data-proxy-postgres:latest",
        driver=None,
        volumes=[(files_dir, "/test-files", "ro")],
    ).with_network(container_network)

    container.start()
    admin_url = container.get_connection_url()

    async def bootstrap() -> None:
        connection = await psycopg.AsyncConnection.connect(admin_url, autocommit=True)
        try:
            await connection.execute("CREATE DATABASE test_template")
            for name, value in PG_DUCKDB_SETTINGS.items():
                await connection.execute(
                    SQL("ALTER DATABASE test_template SET {} = {}").format(
                        Identifier(*name.split(".")), Literal(value)
                    )
                )
        finally:
            await connection.close()

        clone = await psycopg.AsyncConnection.connect(
            urlunsplit(urlsplit(admin_url)._replace(path="/test_template"))
        )
        try:
            await clone.execute(
                render_template("postgres/fixture", {}, root=TEST_SQL_DIR)
            )
            await clone.commit()
        finally:
            await clone.close()

    asyncio.run(bootstrap())

    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def postgres(
    postgres_container: PostgresContainer,
    silo: Silo,
) -> AsyncIterator[Postgres]:
    """Provide the async PostgreSQL boundary for one isolated schema."""
    namespace = PostgresTestNamespace(f"test_{secrets.token_hex(8)}")
    admin_url = postgres_container.get_connection_url()
    dsn = urlunsplit(urlsplit(admin_url)._replace(path="/test_template"))

    setup = await psycopg.AsyncConnection.connect(dsn)
    await setup.execute(SQL("CREATE SCHEMA {}").format(namespace.identifier))
    await setup.commit()
    await setup.close()

    fixture = FILES / "people_partition_10.parquet"
    silo.client.fput_object(
        "test-bucket", f"{namespace.schema}/people/data.parquet", str(fixture)
    )

    connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    await connection.execute(
        render_template(
            "postgres/create_silo_s3_secret",
            {"endpoint": "silo:9000"},
            root=TEST_SQL_DIR,
        ).encode()
    )
    settings.DBOS_SYSTEM_DATABASE_URL = dsn
    await ensure_app_schema(Pg(connection=connection))
    await connection.set_autocommit(False)
    try:
        yield Postgres(connection=connection, dsn=dsn, namespace=namespace)
    finally:
        await connection.rollback()
        await connection.execute("RESET ROLE")
        await connection.commit()
        await connection.close()

        cleanup = await psycopg.AsyncConnection.connect(dsn)
        await cleanup.execute(
            SQL("DROP SCHEMA {} CASCADE").format(namespace.identifier)
        )
        await cleanup.commit()
        await cleanup.close()


@pytest.fixture(name="duckdb")
def duckdb_connection() -> Iterator[DuckDB]:
    """Provide an isolated in-memory DuckDB facade."""
    connection = duckdb.connect(":memory:")

    try:
        yield DuckDB(connection=connection)
    finally:
        connection.close()


@pytest.fixture
async def ducklake_catalog(
    postgres: Postgres,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> Postgres:
    """Publish two DuckLake snapshots of people: 10 rows, then 20 rows.

    Each statement commits alone, so each one creates its own snapshot.
    """
    monkeypatch.setattr(
        settings, "DUCKLAKE_CATALOG_LOCAL_PATH", Path("/var/lib/postgresql/ducklake")
    )
    schema = postgres.namespace.schema
    catalog = CatalogPaths.for_schema(schema).local
    postgres_container.exec(["mkdir", "-p", str(catalog.parent)])

    writer = await psycopg.AsyncConnection.connect(postgres.dsn, autocommit=True)
    try:
        for template, mapping in (
            ("postgres/create_silo_s3_secret", {"endpoint": "silo:9000"}),
            (
                "postgres/publish_people_snapshots",
                {
                    "catalog": str(catalog),
                    "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
                    "first": "s3://test-bucket/app/people/people_partition_10.parquet",
                    "second": "s3://test-bucket/app/people/people_partition_20.parquet",
                },
            ),
        ):
            script = render_template(template, mapping, root=TEST_SQL_DIR)
            for statement in filter(str.strip, script.split(";\n")):
                await writer.execute(statement.encode())
    finally:
        await writer.close()
    return postgres


@pytest.fixture
async def ducklake_view(
    ducklake_catalog: Postgres, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Postgres]:
    """Reconcile the people view over the real DuckLake catalog.

    The published state commits on its own connection, because pg_duckdb refuses
    a transaction that writes both a PostgreSQL table and DuckDB.
    """
    schema = ducklake_catalog.namespace.schema
    table = f"p.{schema}.people"
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.people")])}
    )

    async def source_columns(pg_conn: Pg, table: TableConfig) -> list[tuple[str, str]]:
        return [("cpf", "BIGINT"), ("name", "VARCHAR")]

    monkeypatch.setitem(settings.__dict__, "sync_config", config)
    monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)
    await initialize_schemas(ducklake_catalog.backend, config)
    await reconcile_views(ducklake_catalog.backend, config)
    publisher = await psycopg.AsyncConnection.connect(
        ducklake_catalog.dsn, autocommit=True
    )
    try:
        await write_table_state(
            Pg(connection=publisher),
            table,
            TableState(strategy=Strategy.FULL, signature="s"),
        )
        yield ducklake_catalog
    finally:
        await publisher.execute(
            SQL("DELETE FROM {}.state WHERE table_name = {}").format(
                Identifier(settings.DBOS_APP_SCHEMA), Literal(table)
            )
        )
        await publisher.close()


@pytest.fixture
def psql(postgres_container: PostgresContainer) -> Psql:
    """Provide psql against the test database inside the PostgreSQL container."""
    return Psql(container=postgres_container, database="test_template")


@pytest.fixture(scope="session")
def dbos_schema(postgres_container: PostgresContainer) -> str:
    """Create the DBOS system tables in the test database once."""
    url = urlsplit(postgres_container.get_connection_url())
    engine = create_engine(
        urlunsplit(url._replace(scheme="postgresql+psycopg", path="/test_template"))
    )
    try:
        ensure_dbos_schema(engine, "dbos")
        run_dbos_migrations(engine, "dbos", use_listen_notify=True)
    finally:
        engine.dispose()
    return "dbos"


@pytest.fixture
async def policy_writer(postgres: Postgres, psql: Psql) -> AsyncIterator[str]:
    """Install the access policy in two schemas and a policy writer for the first."""
    schema = postgres.namespace.schema
    other = f"{schema}_other"
    role = f"policy_writer_{schema}"
    scripts = [helm_sql("create_schema", {"schema": Identifier(other)})]
    scripts.extend(
        helm_sql(
            "setup_access_policy",
            {
                "schema": Identifier(target),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_condition(target),
            },
        )
        for target in (schema, other)
    )
    scripts.append(
        helm_sql(
            "setup_policy_writer",
            {
                "schema": Identifier(schema),
                "policy_writer_role": Identifier(role),
                "policy_writer_literal": Literal(role),
                "policy_name": Identifier(role),
            },
        )
    )
    psql.run(psql_script(*scripts))
    try:
        yield role
    finally:
        await postgres.connection.rollback()
        await postgres.connection.execute("RESET ROLE")
        await postgres.connection.commit()
        cleanup = await psycopg.AsyncConnection.connect(postgres.dsn, autocommit=True)
        await cleanup.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(other)))
        await cleanup.execute(SQL("DROP OWNED BY {}").format(Identifier(role)))
        await cleanup.execute(SQL("DROP ROLE {}").format(Identifier(role)))
        await cleanup.close()
