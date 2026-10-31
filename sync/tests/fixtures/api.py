from collections.abc import AsyncIterator, Iterator
from unittest.mock import MagicMock

import duckdb
import psycopg
import pytest
from google.cloud.bigquery import Client, Table
from minio import Minio
from psycopg.sql import SQL, Identifier
from pydantic import SecretStr

from sync.settings import settings
from sync.sources.bigquery.clients import BigQuery
from sync.templates import render_template
from tests.constants import (
    FILES,
    POSTGRES_URL,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_SECRET_KEY,
    TEST_SQL_DIR,
)
from tests.fixtures.types import BigQueryQueryResult, Storage
from tests.helpers import create_state_tables, one_worker_at_a_time
from tests.models import BigQueryMetadataRow, BigQueryPartitionRow
from tests.protocols import BigQueryQueryConfig


@pytest.fixture
async def dbos_conn(
    monkeypatch: pytest.MonkeyPatch, dbos_database: str
) -> AsyncIterator[psycopg.AsyncConnection]:
    """Provide an async connection to the DBOS system database with the data_proxy schema initialized."""
    url = f"{POSTGRES_URL}/{dbos_database}"
    monkeypatch.setattr(settings, "DBOS_SYSTEM_DATABASE_URL", SecretStr(url))
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
            if job_config is not None and len(job_config.query_parameters) > 0
            else ""
        )

        rows = database.execute(
            render_template("bigquery/partitions", {}, root=TEST_SQL_DIR),
            [f"test.dataset.{name}" if len(name) > 0 else ""],
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
def storage(tmp_path_factory: pytest.TempPathFactory) -> Storage:
    """Provide the object storage populated with the Parquet test fixtures."""
    host, _, port = S3_ENDPOINT.partition(":")
    client = Minio(
        S3_ENDPOINT,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
        secure=False,
    )
    with one_worker_at_a_time(tmp_path_factory):
        if not client.bucket_exists("test-bucket"):
            client.make_bucket("test-bucket")
    for fixture in FILES.glob("*.parquet"):
        client.fput_object("test-bucket", f"app/people/{fixture.name}", str(fixture))
        if fixture.name == "people_partition_10.parquet":
            client.fput_object("test-bucket", "app/people/data.parquet", str(fixture))
    return Storage(host, int(port), client)
