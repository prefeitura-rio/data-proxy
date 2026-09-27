"""Database and model fixtures."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import duckdb
import psycopg
import pytest
from psycopg.sql import SQL
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.network import Network

from data_proxy.bigquery.config import PartitionKindConfig
from data_proxy.duckdb import DuckDB
from data_proxy.models import (
    DumpTask,
    TaskSelection,
    UnitMapping,
)
from data_proxy.models import (
    Table as TableModel,
)
from data_proxy.postgres import Postgres as Pg
from data_proxy.settings import settings
from data_proxy.state import ensure_app_schema
from data_proxy.templates import render_template
from tests.constants import FILES
from tests.fixtures.types import Postgres, PostgresTestNamespace, SeaweedFS
from tests.helpers import TEST_SQL_DIR


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
    seaweedfs: SeaweedFS,
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
    seaweedfs.client.fput_object(
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


@pytest.fixture
async def duckdb_raw_query_stub(postgres: Postgres) -> AsyncIterator[None]:
    """Restore the extension raw-query function after a fallback test stub."""
    cursor = await postgres.connection.execute(
        "SELECT pg_get_functiondef('duckdb.raw_query(text)'::regprocedure)"
    )
    row = await cursor.fetchone()
    assert row is not None
    definition = row[0]
    assert isinstance(definition, str)
    try:
        yield
    finally:
        await postgres.connection.rollback()
        await postgres.connection.execute(definition.encode())
        await postgres.connection.commit()
        await postgres.connection.execute(
            "SELECT duckdb.raw_query('CREATE OR REPLACE VIEW restore_check AS SELECT 1 AS id')"
        )
        await postgres.connection.commit()
        cursor = await postgres.connection.execute(
            "SELECT * FROM duckdb.query('SELECT id FROM restore_check')"
        )
        assert await cursor.fetchall() == [(1,)]
        await postgres.connection.commit()


@pytest.fixture(name="duckdb")
def duckdb_connection() -> Iterator[DuckDB]:
    """Provide an isolated in-memory DuckDB facade."""
    connection = duckdb.connect(":memory:")

    try:
        yield DuckDB(connection=connection)
    finally:
        connection.close()
