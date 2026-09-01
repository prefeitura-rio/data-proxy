"""Shared fixtures for the data-proxy test suite."""

import secrets
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import LiteralString, cast
from unittest.mock import MagicMock
from urllib.parse import urlsplit, urlunsplit

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
    ) as broker:
        yield broker


@pytest.fixture
def bigquery() -> Iterator[Client]:
    """Provide an isolated DuckDB-backed BigQuery client mock."""
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
def postgres_container() -> Iterator[PostgresContainer]:
    """Provide one PostgreSQL container and an initialized template database."""
    container = PostgresContainer(
        "ghcr.io/prefeitura-rio/data-proxy-postgres:latest",
        driver=None,
    )
    container.start()
    admin_url = container.get_connection_url()

    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute("CREATE DATABASE test_template")

    with psycopg.connect(
        urlunsplit(urlsplit(admin_url)._replace(path="/test_template"))
    ) as connection:
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
        yield container
    finally:
        container.stop()


@pytest.fixture(name="postgres")
def postgres_connection(
    postgres_container: PostgresContainer,
) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """Provide an isolated PostgreSQL database cloned from the template."""
    admin_url = postgres_container.get_connection_url()
    database = f"test_{secrets.randbelow(10**16):016d}"

    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(
            SQL("CREATE DATABASE {} TEMPLATE test_template").format(
                Identifier(database)
            )
        )

    connection = psycopg.connect(
        urlunsplit(urlsplit(admin_url)._replace(path=f"/{database}"))
    )
    try:
        yield connection
    finally:
        connection.close()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                cast(
                    LiteralString,
                    load_template(
                        TemplateSpec(
                            path="postgres/terminate_connections",
                            mapping={},
                        ),
                        FILES.parent / "sql",
                    ),
                ),
                (database,),
            )
            admin.execute(
                SQL("DROP DATABASE IF EXISTS {}").format(Identifier(database))
            )


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


@pytest.fixture(name="duckdb")
def duckdb_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    """Provide an isolated in-memory DuckDB connection."""
    connection = duckdb.connect(":memory:")

    try:
        yield connection
    finally:
        connection.close()
