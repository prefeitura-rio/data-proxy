import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import duckdb
import psycopg
import pytest
from dbos._migration import ensure_dbos_schema, run_dbos_migrations
from psycopg.sql import SQL, Identifier, Literal
from pydantic import SecretStr
from sqlalchemy import create_engine

from sync.duckdb import DuckDB
from sync.ducklake import DuckLakePaths
from sync.models import (
    FullTable,
    SchemaConfig,
    Strategy,
    SyncConfig,
    TableConfig,
    TableState,
)
from sync.postgres import Postgres as Pg
from sync.settings import settings
from sync.state import write_table_state
from sync.templates import render_template
from sync.views import stages
from sync.views.reconcile import reconcile_views
from tests.constants import (
    DUCKDB_DATABASE_SETTINGS,
    FILES,
    POSTGRES_URL,
    S3_SECRET_INPUTS,
    TEST_DATABASE,
    TEST_SQL_DIR,
    WORKER,
)
from tests.fixtures.types import (
    Postgres,
    PostgresTestNamespace,
    Psql,
    Storage,
)
from tests.helpers import (
    act_as_token,
    create_state_tables,
    initialize_schemas,
    one_worker_at_a_time,
    publish_people_catalog,
)


@pytest.fixture(scope="session")
def database(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Give the database that this worker uses. Each xdist worker clones its own from test_template, so workers do not share the functions that the templates install."""
    if WORKER is None:
        return TEST_DATABASE

    with (
        one_worker_at_a_time(tmp_path_factory),
        psycopg.connect(f"{POSTGRES_URL}/postgres", autocommit=True) as admin,
    ):
        name = Identifier(TEST_DATABASE)
        admin.execute(SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
        admin.execute(SQL("CREATE DATABASE {} TEMPLATE test_template").format(name))
        for setting in DUCKDB_DATABASE_SETTINGS:
            admin.execute(
                SQL("ALTER DATABASE {} SET {} = 'on'").format(name, SQL(setting))
            )

    return TEST_DATABASE


@pytest.fixture
async def postgres(storage: Storage, database: str) -> AsyncIterator[Postgres]:
    """Provide the async PostgreSQL boundary for one isolated schema."""
    namespace = PostgresTestNamespace(f"test_{secrets.token_hex(8)}")
    dsn = f"{POSTGRES_URL}/{database}"

    setup = await psycopg.AsyncConnection.connect(dsn)
    await setup.execute(SQL("CREATE SCHEMA {}").format(namespace.identifier))
    await setup.commit()
    await setup.close()

    fixture = FILES / "people_partition_10.parquet"
    storage.client.fput_object(
        "test-bucket", f"{namespace.schema}/people/data.parquet", str(fixture)
    )

    connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    await connection.execute(
        render_template(
            "postgres/create_s3_secret",
            S3_SECRET_INPUTS,
            root=TEST_SQL_DIR,
        ).encode()
    )
    settings.DBOS_SYSTEM_DATABASE_URL = SecretStr(dsn)
    await create_state_tables(connection)
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


async def drop_catalog_schema(name: str) -> None:
    """Remove one metadata schema from the catalog database."""
    catalog = await psycopg.AsyncConnection.connect(
        settings.DUCKLAKE_CATALOG_URL.get_secret_value(), autocommit=True
    )
    try:
        await catalog.execute(
            SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(Identifier(name))
        )
    finally:
        await catalog.close()


@pytest.fixture
async def catalog_paths(tmp_path: Path) -> AsyncIterator[DuckLakePaths]:
    """Give the test an empty metadata schema of its own in the catalog database."""
    schema = f"app_{secrets.token_hex(4)}"
    await drop_catalog_schema(schema)
    try:
        yield DuckLakePaths(
            catalog=f"ducklake:postgres:{settings.DUCKLAKE_CATALOG_URL.get_secret_value()}",
            data=str(tmp_path / "data"),
            metadata_schema=schema,
        )
    finally:
        await drop_catalog_schema(schema)


@pytest.fixture
async def ducklake_catalog(postgres: Postgres) -> Postgres:
    """Publish two DuckLake snapshots of people: 10 rows, then 20 rows.

    The catalog comes from the DuckDB that the sync worker uses, in the catalog
    database. The serving database only reads it, with the reader secrets that the test
    database setup creates. The metadata schema stays after the test, because the reader keeps it locked.
    Each statement commits alone, so each one creates its own snapshot.
    """
    schema = postgres.namespace.schema
    publish_people_catalog(
        schema,
        f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
    )
    writer = await psycopg.AsyncConnection.connect(postgres.dsn, autocommit=True)
    try:
        script = render_template(
            "postgres/create_s3_secret",
            S3_SECRET_INPUTS,
            root=TEST_SQL_DIR,
        )
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

    monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)
    await initialize_schemas(ducklake_catalog.backend, config)
    await reconcile_views(ducklake_catalog.backend, config)
    await act_as_token(ducklake_catalog, "dp_api", schema)
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


@pytest.fixture(scope="session")
def dbos_schema(tmp_path_factory: pytest.TempPathFactory, database: str) -> str:
    """Create the DBOS system tables in the test database once for each worker, one worker at a time."""
    engine = create_engine(
        POSTGRES_URL.replace("postgresql://", "postgresql+psycopg://", 1)
        + f"/{database}"
    )
    try:
        with one_worker_at_a_time(tmp_path_factory):
            ensure_dbos_schema(engine, "dbos")
            run_dbos_migrations(engine, "dbos", use_listen_notify=True)
    finally:
        engine.dispose()
    return "dbos"


@pytest.fixture
def psql(database: str) -> Psql:
    """Provide psql against the test database of the PostgreSQL test server."""
    return Psql(database=database)
