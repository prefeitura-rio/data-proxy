from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import duckdb
import psycopg
import pytest
from google.cloud.bigquery import Client, Table
from minio import Minio
from psycopg.sql import SQL, Identifier

from sync.settings import Settings, settings
from sync.sources.bigquery.clients import BigQuery
from sync.templates import render_template
from tests.constants import (
    FILES,
    POSTGRES_URL,
    SILO_ENDPOINT,
    TEST_SQL_DIR,
    VALKEY_PORT,
)
from tests.fixtures.types import BigQueryQueryResult, Silo
from tests.helpers import create_state_tables
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


@pytest.fixture
async def dbos_conn(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[psycopg.AsyncConnection]:
    """Provide an async connection to the DBOS system database with the data_proxy schema initialized."""
    url = f"{POSTGRES_URL}/dbos_system"
    monkeypatch.setattr(settings, "DBOS_SYSTEM_DATABASE_URL", url)
    conn = await psycopg.AsyncConnection.connect(url, autocommit=True)
    await create_state_tables(conn)
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
def silo() -> Silo:
    """Provide the Silo object storage populated with the Parquet test fixtures."""
    host, _, port = SILO_ENDPOINT.partition(":")
    client = Minio(
        SILO_ENDPOINT,
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
    )
    if not client.bucket_exists("test-bucket"):
        client.make_bucket("test-bucket")
    for fixture in FILES.glob("*.parquet"):
        client.fput_object("test-bucket", f"app/people/{fixture.name}", str(fixture))
        if fixture.name == "people_partition_10.parquet":
            client.fput_object("test-bucket", "app/people/data.parquet", str(fixture))
    return Silo(host, int(port), client)


@pytest.fixture(scope="session")
def valkey() -> tuple[str, int]:
    """Provide the Valkey endpoint for cache integration tests."""
    return "127.0.0.1", VALKEY_PORT
