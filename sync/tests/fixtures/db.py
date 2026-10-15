import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import duckdb
import psycopg
import pytest
from dbos._migration import ensure_dbos_schema, run_dbos_migrations
from psycopg.sql import SQL, Identifier, Literal
from sqlalchemy import create_engine

from sync.duckdb import DuckDB
from sync.ducklake import DuckLakePaths
from sync.models import (
    DumpTask,
    FullTable,
    SchemaConfig,
    Strategy,
    SyncConfig,
    TableConfig,
    TableState,
    UnitMapping,
)
from sync.models import (
    Table as TableModel,
)
from sync.postgres import Postgres as Pg
from sync.settings import settings
from sync.sources.partitions import PartitionKindConfig, TaskSelection
from sync.state import write_table_state
from sync.templates import render_template
from sync.views import stages
from sync.views.reconcile import reconcile_views
from tests.constants import FILES, POSTGRES_URL, SILO_ENDPOINT, TEST_SQL_DIR
from tests.fixtures.types import (
    Postgres,
    PostgresTestNamespace,
    Psql,
    Silo,
)
from tests.helpers import (
    create_state_tables,
    helm_sql,
    initialize_schemas,
    psql_script,
    publish_people_catalog,
    schema_scope_condition,
)


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


@pytest.fixture
async def postgres(silo: Silo) -> AsyncIterator[Postgres]:
    """Provide the async PostgreSQL boundary for one isolated schema."""
    namespace = PostgresTestNamespace(f"test_{secrets.token_hex(8)}")
    dsn = f"{POSTGRES_URL}/test_template"

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
            {"endpoint": SILO_ENDPOINT},
            root=TEST_SQL_DIR,
        ).encode()
    )
    settings.DBOS_SYSTEM_DATABASE_URL = dsn
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
        settings.DUCKLAKE_CATALOG_URL, autocommit=True
    )
    try:
        await catalog.execute(
            SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(Identifier(name))
        )
    finally:
        await catalog.close()


@pytest.fixture
async def catalog_paths(tmp_path: Path) -> AsyncIterator[DuckLakePaths]:
    """Give the test an empty metadata schema named app in the catalog database."""
    await drop_catalog_schema("app")
    try:
        yield DuckLakePaths(
            catalog=f"ducklake:postgres:{settings.DUCKLAKE_CATALOG_URL}",
            data=str(tmp_path / "data"),
            metadata_schema="app",
        )
    finally:
        await drop_catalog_schema("app")


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
            "postgres/create_silo_s3_secret",
            {"endpoint": SILO_ENDPOINT},
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
def psql() -> Psql:
    """Provide psql against the test database of the PostgreSQL test server."""
    return Psql(database="test_template")


@pytest.fixture(scope="session")
def dbos_schema() -> str:
    """Create the DBOS system tables in the test database once."""
    engine = create_engine(
        POSTGRES_URL.replace("postgresql://", "postgresql+psycopg://", 1)
        + "/test_template"
    )
    try:
        ensure_dbos_schema(engine, "dbos")
        run_dbos_migrations(engine, "dbos", use_listen_notify=True)
    finally:
        engine.dispose()
    return "dbos"


@pytest.fixture
async def admin_db() -> AsyncIterator[psycopg.AsyncConnection]:
    """Provide a UTC connection to the migrated test database and roll it back afterward."""
    connection = await psycopg.AsyncConnection.connect(f"{POSTGRES_URL}/test")
    await connection.execute("SET TIME ZONE 'UTC'")
    try:
        yield connection
    finally:
        await connection.rollback()
        await connection.close()


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
                "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
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
