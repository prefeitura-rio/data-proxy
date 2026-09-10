"""Shared fixtures for the data-proxy test suite."""

import secrets
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

import duckdb
import psycopg
import pytest
from fakeredis import FakeAsyncRedis
from faststream.redis import RedisBroker, TestRedisBroker
from google.cloud.bigquery import (
    Client,
    Row,
    Table,
)
from minio import Minio
from psycopg.sql import SQL, Identifier
from redis.asyncio import Redis
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

from dp.bigquery.config import PartitionKindConfig
from dp.models import (
    AllSelection,
    DumpTask,
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    SchemaWriters,
    TaskSelection,
    UnitMapping,
)
from dp.settings import Settings, settings
from dp.sync.dumper import broker as dumper_broker
from dp.sync.producer import broker as producer_broker
from dp.sync.publisher import broker as publisher_broker
from dp.sync.seeder import broker as seeder_broker
from dp.templates import TemplateSpec, load_template
from tests.constants import FILES
from tests.helpers import execute_sql
from tests.models import BigQueryMetadataRow, BigQueryPartitionRow
from tests.protocols import BigQueryQueryConfig


@dataclass(frozen=True, slots=True)
class PostgresTestNamespace:
    """One per-test PostgreSQL schema and its related identifiers."""

    schema: str

    @property
    def identifier(self) -> Identifier:
        """Return the safely quoted schema identifier."""
        return Identifier(self.schema)

    def table(self, name: str) -> Identifier:
        """Return a safely quoted table identifier in this namespace."""
        return Identifier(self.schema, name)

    def policy(self, name: str) -> Identifier:
        """Return a safely quoted policy identifier."""
        return Identifier(name)


Tracker = Callable[[Callable[..., object]], Callable[..., object]]


@pytest.fixture
def mock_push_to_gateway() -> object:
    """Prevent real HTTP calls to Pushgateway during tests."""

    def fake_tracker(job: str) -> Tracker:
        def decorator(function: Callable[..., object]) -> Callable[..., object]:
            return function

        return decorator

    with (
        patch("dp.metrics.push_to_gateway", new_callable=AsyncMock),
        patch("dp.sync.publisher.tracker", fake_tracker),
        patch("dp.sync.dumper.tracker", fake_tracker),
        patch("dp.sync.seeder.tracker", fake_tracker),
        patch("dp.sync.producer.tracker", fake_tracker),
    ):
        yield


@pytest.fixture
def sync_config_path(tmp_path: Path) -> Path:
    """Provide the synchronization configuration file path."""
    path = tmp_path / "sync.json"
    path.write_text('{"schemas": {}}')
    return path


@pytest.fixture
def redis() -> Redis:
    """Return fakeredis state for deterministic Redis API and state tests.

    This fixture is not a Valkey stream-compatibility integration boundary.
    """
    return cast(Redis, FakeAsyncRedis())


@pytest.fixture
def redis_factory() -> Callable[[Redis], Callable[[Settings, int | None], Redis]]:
    """Return a factory for typed Settings.redis method replacements."""

    def factory(redis_client: Redis) -> Callable[[Settings, int | None], Redis]:
        def method(settings_instance: Settings, db: int | None = None) -> Redis:
            return redis_client

        return method

    return factory


@pytest.fixture
def schema_writers() -> SchemaWriters:
    """Return the shared schema writer configuration for tests."""
    return SchemaWriters(
        writers={"app": "postgresql://writer", "other": "postgresql://writer"}
    )


@pytest.fixture
def standard_dump_task() -> DumpTask:
    """Return a standard dump task for tests."""
    return DumpTask(
        run_id="r1",
        table="p.d.t",
        bucket_path="s3://b/t",
        selection=AllSelection(),
    )


@pytest.fixture
def invalid_rls() -> list[UnitMapping]:
    """Return an invalid runtime RLS value for guard tests."""
    return cast("list[UnitMapping]", cast(object, "invalid"))


@pytest.fixture
def invalid_partition_plan() -> PartitionedTablePlan:
    """Return an invalid partition plan for guard tests."""
    return cast("PartitionedTablePlan", cast(object, "invalid"))


@pytest.fixture
def invalid_physical_partition() -> PhysicalPartition:
    """Return a physical partition with an invalid selection."""
    return cast(
        "PhysicalPartition",
        cast(object, SimpleNamespace(selection=object())),
    )


@pytest.fixture
def invalid_dump_task() -> DumpTask:
    """Return a dump task with an invalid selection."""
    return cast(
        DumpTask,
        cast(
            object,
            type(
                "InvalidTask",
                (),
                {
                    "table": "p.d.t",
                    "bucket_path": "s3://b",
                    "json_columns": [],
                    "selection": object(),
                },
            )(),
        ),
    )


@pytest.fixture
def invalid_partition_row() -> Row:
    """Return an invalid partition row for guard tests."""
    return cast(
        "Row",
        cast(object, {"partition_id": "1", "last_modified_time": datetime.now(UTC)}),
    )


@pytest.fixture
def invalid_kind_config() -> PartitionKindConfig:
    """Return an invalid partition kind config for guard tests."""
    return cast("PartitionKindConfig", cast(object, "invalid"))


@pytest.fixture
def invalid_selection() -> TaskSelection:
    """Return an unknown task selection for guard tests."""
    return cast("TaskSelection", object())


@pytest.fixture
def full_table() -> FullTable:
    """Return a full table in the app schema for tests."""
    return FullTable(name="p.app.t", resolved_schema="app")


@pytest.fixture
def partitioned_table() -> PartitionedTable:
    """Return a partitioned table in the app schema for tests."""
    return PartitionedTable(name="p.app.t", resolved_schema="app")


@pytest.fixture
def test_settings(
    monkeypatch: pytest.MonkeyPatch,
    redis: Redis,
    schema_writers: SchemaWriters,
    sync_config_path: Path,
) -> Settings:
    """Provide settings configured with test dependency objects."""
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", sync_config_path)
    monkeypatch.setattr(Settings, "redis", lambda self, db=None: redis)  # pyright: ignore[reportUnknownLambdaType,reportUnknownArgumentType]
    monkeypatch.setattr(Settings, "schema_writers", property(lambda _: schema_writers))
    return settings


@pytest.fixture
async def broker() -> AsyncIterator[tuple[RedisBroker, ...]]:
    """Provide an in-memory broker for all application Redis brokers."""
    async with TestRedisBroker(
        producer_broker,
        dumper_broker,
        seeder_broker,
        publisher_broker,
        connect_only=False,
    ) as broker:
        yield broker


@pytest.fixture
def bigquery() -> Iterator[Client]:
    """Provide a deterministic DuckDB-backed BigQuery API mock.

    This fixture does not call GCP and does not validate the Google API service.
    """
    database = duckdb.connect(":memory:")
    database.read_csv(FILES / "partitions.csv", all_varchar=True).create_view(
        "partition_metadata"
    )
    database.read_csv(FILES / "metadata.csv", all_varchar=True).create_view(
        "table_metadata"
    )

    client = MagicMock(spec=Client)

    def get_table(table: str, **_: object) -> Table:
        """Return metadata for one preseeded table."""
        name = table.replace(":", ".")
        row = database.execute(
            load_template(
                TemplateSpec(
                    path="bigquery/table_metadata",
                    mapping={"table_name": name},
                ),
                FILES.parent / "sql",
            )
        ).fetchone()

        if row is None:
            raise KeyError(f"BigQuery table is not preseeded: {name}")

        metadata = BigQueryMetadataRow.model_validate(row[0])

        return metadata.to_table()

    def query(
        _: str,
        job_config: BigQueryQueryConfig | None = None,
        **__: object,
    ) -> object:
        """Return validated partition rows for the requested preseeded table."""
        name = (
            job_config.query_parameters[0].value
            if job_config is not None and job_config.query_parameters
            else ""
        )

        rows = database.execute(
            load_template(
                TemplateSpec(path="bigquery/partitions", mapping={}),
                FILES.parent / "sql",
            ),
            [f"test.dataset.{name}" if name else ""],
        ).fetchall()

        partitions = [BigQueryPartitionRow.model_validate(row[0]) for row in rows]

        return MagicMock(
            result=lambda: [partition.model_dump() for partition in partitions]
        )

    client.get_table.side_effect = get_table
    client.query.side_effect = query

    try:
        yield client
    finally:
        database.close()


@pytest.fixture(scope="session")
def silo_container(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[str, int]]:
    """Provide Silo object storage populated with the Parquet test fixtures."""
    credentials = tmp_path_factory.mktemp("silo")
    (credentials / "access_key").write_text("minioadmin")
    (credentials / "secret_key").write_text("minioadmin")
    container = (
        DockerContainer("docker.io/pgsty/silo")
        .with_command("server /data")
        .with_volume_mapping(str(credentials / "access_key"), "/access_key", "ro")
        .with_volume_mapping(str(credentials / "secret_key"), "/secret_key", "ro")
        .with_env("MINIO_ROOT_USER_FILE", "/access_key")
        .with_env("MINIO_ROOT_PASSWORD_FILE", "/secret_key")
        .with_exposed_ports(9000)
    )
    container.start()
    endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}/minio/health/live"
    deadline = monotonic() + 10
    while monotonic() < deadline:
        try:
            with urlopen(endpoint, timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            sleep(0.1)
    else:
        container.stop()
        raise RuntimeError("Silo did not become ready within 10 seconds")
    client = Minio(
        f"{container.get_container_host_ip()}:{container.get_exposed_port(9000)}",
        access_key="minioadmin",
        secret_key="minioadmin",  # noqa: S106
        secure=False,
    )
    client.make_bucket("test-bucket")
    for fixture in FILES.glob("*.parquet"):
        client.fput_object("test-bucket", f"app/people/{fixture.name}", str(fixture))
        if fixture.name == "people_partition_10.parquet":
            client.fput_object("test-bucket", "app/people/data.parquet", str(fixture))
    try:
        yield container.get_container_host_ip(), container.get_exposed_port(9000)
    finally:
        container.stop()


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Provide the real PostgreSQL and pg_duckdb integration boundary."""
    files_dir = str((Path(__file__).parent / "files").absolute())

    container = PostgresContainer(
        "ghcr.io/prefeitura-rio/data-proxy-postgres:latest",
        driver=None,
        volumes=[(files_dir, "/test-files", "ro")],
    )

    container.start()
    admin_url = container.get_connection_url()

    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute("CREATE DATABASE test_template")

    with psycopg.connect(
        urlunsplit(urlsplit(admin_url)._replace(path="/test_template"))
    ) as connection:
        execute_sql(connection, "postgres/fixture")

    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
def namespace(
    postgres: psycopg.Connection[tuple[object, ...]],
) -> Iterator[PostgresTestNamespace]:
    """Provide and clean one isolated schema in the cloned test database."""
    namespace = PostgresTestNamespace(f"test_{secrets.token_hex(8)}")
    postgres.execute(SQL("CREATE SCHEMA {}").format(namespace.identifier))
    postgres.commit()
    try:
        yield namespace
    finally:
        if not postgres.autocommit:
            postgres.rollback()
        postgres.execute("RESET ROLE")
        postgres.commit()
        postgres.execute(SQL("DROP SCHEMA {} CASCADE").format(namespace.identifier))
        postgres.commit()


@pytest.fixture(name="postgres")
def postgres_connection(
    postgres_container: PostgresContainer,
) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """Provide a reset session database for each PostgreSQL test connection."""
    admin_url = postgres_container.get_connection_url()
    connection = psycopg.connect(
        urlunsplit(urlsplit(admin_url)._replace(path="/test_template"))
    )
    try:
        yield connection
    finally:
        connection.rollback()
        connection.execute("RESET ROLE")
        connection.commit()
        connection.close()


@pytest.fixture
def duckdb_raw_query_stub(
    postgres: psycopg.Connection[tuple[object, ...]],
) -> Iterator[None]:
    """Restore the extension raw-query function after a fallback test stub."""
    row = postgres.execute(
        "SELECT pg_get_functiondef('duckdb.raw_query(text)'::regprocedure)"
    ).fetchone()
    assert row is not None
    definition = cast(str, row[0])
    try:
        yield
    finally:
        postgres.rollback()
        postgres.execute(definition.encode())
        postgres.commit()
        postgres.execute(
            "SELECT duckdb.raw_query('CREATE OR REPLACE VIEW restore_check AS SELECT 1 AS id')"
        )
        postgres.commit()
        assert postgres.execute(
            "SELECT * FROM duckdb.query('SELECT id FROM restore_check')"
        ).fetchall() == [(1,)]
        postgres.commit()


@pytest.fixture
def postgres_silo(
    postgres: psycopg.Connection[tuple[object, ...]],
    silo_container: tuple[str, int],
    namespace: PostgresTestNamespace,
) -> psycopg.Connection[tuple[object, ...]]:
    """Configure the cloned pg_duckdb database for the Silo test bucket."""
    host, port = silo_container
    client = Minio(
        f"{host}:{port}",
        access_key="minioadmin",
        secret_key="minioadmin",  # noqa: S106
        secure=False,
    )
    fixture = FILES / "people_partition_10.parquet"
    client.fput_object(
        "test-bucket", f"{namespace.schema}/people/data.parquet", str(fixture)
    )
    execute_sql(
        postgres,
        "postgres/create_silo_s3_secret",
        mapping={"endpoint": f"host.containers.internal:{port}"},
    )
    postgres.commit()
    return postgres


@pytest.fixture
def freshness_tables(
    postgres: psycopg.Connection[tuple[object, ...]],
    namespace: PostgresTestNamespace,
) -> tuple[FullTable, PartitionedTable, str]:
    """Create freshness metadata in one isolated test schema."""
    execute_sql(
        postgres,
        "postgres/create_freshness_table",
        mapping={"schema": namespace.schema},
    )
    postgres.commit()
    return (
        FullTable(name=f"p.{namespace.schema}.full", resolved_schema=namespace.schema),
        PartitionedTable(
            name=f"p.{namespace.schema}.partitioned", resolved_schema=namespace.schema
        ),
        namespace.schema,
    )


@pytest.fixture
def postgres_dsn(
    postgres: psycopg.Connection[tuple[object, ...]],
    postgres_container: PostgresContainer,
) -> str:
    """Return the writer DSN for the isolated cloned PostgreSQL database."""
    return urlunsplit(
        urlsplit(postgres_container.get_connection_url())._replace(
            path=f"/{postgres.info.dbname}"
        )
    )


@pytest.fixture(name="duckdb")
def duckdb_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    """Provide an isolated in-memory DuckDB connection."""
    connection = duckdb.connect(":memory:")

    try:
        yield connection
    finally:
        connection.close()
