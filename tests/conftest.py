"""Shared fixtures for the data-proxy test suite."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import LiteralString, cast
from unittest.mock import MagicMock

import pytest
from duckdb import DuckDBPyConnection, connect
from fakeredis import FakeAsyncRedis
from faststream.redis import RedisBroker, TestRedisBroker
from google.cloud.bigquery import (
    Client,
    Table,
)
from minio import Minio
from psycopg import Connection
from psycopg import connect as connect_postgres
from psycopg.sql import SQL
from redis.asyncio import Redis
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

from dp.models import SchemaWriters
from dp.settings import Settings, settings
from dp.sync.dumper import broker as dumper_broker
from dp.sync.producer import broker as producer_broker
from dp.sync.publisher import broker as publisher_broker
from dp.sync.seeder import broker as seeder_broker
from dp.templates import TemplateSpec, load_template
from tests.constants import FILES
from tests.models import BigQueryMetadataRow, BigQueryPartitionRow
from tests.protocols import BigQueryQueryConfig


@pytest.fixture
def sync_config_path(tmp_path: Path) -> Path:
    """Provide the synchronization configuration file path."""
    path = tmp_path / "sync.json"
    path.write_text('{"schemas": {}}')
    return path


@pytest.fixture
def redis() -> Redis:
    """Return isolated in-memory Redis state."""
    return cast(Redis, FakeAsyncRedis())


@pytest.fixture
def schema_writers() -> SchemaWriters:
    """Return the shared schema writer configuration for tests."""
    return SchemaWriters(
        writers={"app": "postgresql://writer", "other": "postgresql://writer"}
    )


@pytest.fixture(autouse=True)
def test_settings(
    monkeypatch: pytest.MonkeyPatch,
    redis: Redis,
    schema_writers: SchemaWriters,
    sync_config_path: Path,
) -> Settings:
    """Provide settings configured with test dependency objects."""
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", sync_config_path)
    monkeypatch.setattr(Settings, "redis", property(lambda _: redis))
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
    ) as b:
        yield b


@pytest.fixture
def bigquery() -> Iterator[Client]:
    """Provide an isolated DuckDB-backed BigQuery client mock."""
    database = connect(":memory:")
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


@pytest.fixture
def postgres() -> Iterator[Connection[tuple[object, ...]]]:
    """Provide a PostgreSQL connection with the `app` schema and read role."""
    container = PostgresContainer(
        "ghcr.io/prefeitura-rio/data-proxy-postgres:latest",
        driver=None,
    )

    container.start()
    connection = connect_postgres(container.get_connection_url())

    connection.execute(
        SQL(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/fixture", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        )
    )

    try:
        yield connection
    finally:
        connection.close()
        container.stop()


@pytest.fixture
def minio() -> Iterator[Minio]:
    """Provide a MinIO-compatible client backed by a Silo container."""
    container = DockerContainer("docker.io/pgsty/silo:latest")
    container.with_env("MINIO_ROOT_USER", "minioadmin")
    container.with_env("MINIO_ROOT_PASSWORD", "minioadmin")
    container.with_exposed_ports(9000).start()

    client = Minio(
        f"{container.get_container_host_ip()}:{container.get_exposed_port(9000)}",
        access_key="minioadmin",
        secret_key="minioadmin",  # noqa: S106
        secure=False,
    )

    try:
        yield client
    finally:
        container.stop()


@pytest.fixture
def duckdb() -> Iterator[DuckDBPyConnection]:
    """Provide an isolated in-memory DuckDB connection."""
    connection = connect(":memory:")

    try:
        yield connection
    finally:
        connection.close()
