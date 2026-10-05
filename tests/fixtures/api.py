"""API and external service fixtures."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import AsyncMock, MagicMock
from urllib.request import urlopen

import duckdb
import psycopg
import pytest
from google.cloud.bigquery import Client, Table
from minio import Minio
from psycopg.sql import SQL, Identifier
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from data_proxy.settings import Settings, settings
from data_proxy.sources.bigquery.clients import BigQuery
from data_proxy.templates import render_template
from tests.constants import FILES, TEST_SQL_DIR
from tests.fixtures.types import BigQueryQueryResult, Silo
from tests.models import BigQueryMetadataRow, BigQueryPartitionRow
from tests.protocols import BigQueryQueryConfig


@pytest.fixture
def sync_config_path(tmp_path: Path) -> Path:
    """Provide the synchronization configuration file path."""
    path = tmp_path / "sync.json"
    path.write_text('{"schemas": {}}')
    return path


@pytest.fixture
def redis() -> MagicMock:
    """Return a mock async Redis client for cache tests."""

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.flushdb = AsyncMock()
    return client


@pytest.fixture
def test_settings(
    monkeypatch: pytest.MonkeyPatch,
    sync_config_path: Path,
) -> Settings:
    """Provide settings configured with test dependency objects."""
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", sync_config_path)
    return settings


@pytest.fixture(scope="session")
def system_db_container() -> Iterator[PostgresContainer]:
    """Provide a Postgres container for the DBOS system database and application state."""
    container = PostgresContainer("postgres:17-alpine")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def dbos_conn(
    system_db_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[psycopg.AsyncConnection]:
    """Provide an async connection to the DBOS system database with the data_proxy schema initialized."""
    from data_proxy.postgres import Postgres
    from data_proxy.state import ensure_app_schema

    url = system_db_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    monkeypatch.setattr(settings, "DBOS_SYSTEM_DATABASE_URL", url)
    conn = await psycopg.AsyncConnection.connect(url, autocommit=True)
    await ensure_app_schema(Postgres(connection=conn))
    await conn.execute(
        SQL("TRUNCATE {}.state, {}.errors").format(
            Identifier(settings.DBOS_APP_SCHEMA), Identifier(settings.DBOS_APP_SCHEMA)
        )
    )
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def bigquery() -> Iterator[BigQuery]:
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

    def get_table(table: str) -> Table:
        """Return metadata for one preseeded table."""
        name = table.replace(":", ".")
        row = database.execute(
            render_template("bigquery/table_metadata", {}, root=TEST_SQL_DIR), [name]
        ).fetchone()

        if row is None:
            raise KeyError(f"BigQuery table is not preseeded: {name}")

        metadata = BigQueryMetadataRow.model_validate(row[0])

        return metadata.to_table()

    def query(
        sql: str, job_config: BigQueryQueryConfig | None = None
    ) -> BigQueryQueryResult:
        """Return validated partition rows for the requested preseeded table."""
        name = (
            job_config.query_parameters[0].value
            if job_config is not None and job_config.query_parameters
            else ""
        )

        rows = database.execute(
            render_template("bigquery/partitions", {}, root=TEST_SQL_DIR),
            [f"test.dataset.{name}" if name else ""],
        ).fetchall()

        partitions = [BigQueryPartitionRow.model_validate(row[0]) for row in rows]

        return BigQueryQueryResult(
            rows=[partition.model_dump() for partition in partitions]
        )

    client.get_table.side_effect = get_table
    client.query.side_effect = query

    try:
        yield BigQuery(client=client)
    finally:
        database.close()


@pytest.fixture(scope="session")
def container_network() -> Iterator[Network]:
    """Provide a network on which the containers reach each other by name."""
    with Network() as network:
        yield network


@pytest.fixture(scope="session")
def silo(
    container_network: Network,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Silo]:
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
        .with_network(container_network)
        .with_network_aliases("silo")
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
        yield Silo(
            container.get_container_host_ip(),
            container.get_exposed_port(9000),
            client,
        )
    finally:
        container.stop()


@pytest.fixture(scope="session")
def valkey() -> Iterator[tuple[str, int]]:
    """Provide a real Valkey endpoint for cache integration tests."""
    container = DockerContainer("valkey/valkey:8-alpine").with_exposed_ports(6379)
    container.start()
    try:
        yield container.get_container_host_ip(), int(container.get_exposed_port(6379))
    finally:
        container.stop()
