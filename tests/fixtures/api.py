"""API and external service fixtures."""

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from time import monotonic, sleep
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.request import urlopen

import duckdb
import pytest
from fakeredis import FakeAsyncRedis
from faststream.redis import RedisBroker, TestRedisBroker
from google.cloud.bigquery import Client, Table
from minio import Minio
from redis.asyncio import Redis
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from dp.models import AllSelection, DumpTask, SchemaWriters
from dp.settings import Settings, settings
from dp.sync.dumper import broker as dumper_broker
from dp.sync.producer import broker as producer_broker
from dp.sync.publisher import broker as publisher_broker
from dp.sync.seeder import broker as seeder_broker
from dp.templates import render_template
from tests.constants import FILES
from tests.fixtures.types import SeaweedFS
from tests.models import BigQueryMetadataRow, BigQueryPartitionRow
from tests.protocols import BigQueryQueryConfig

Tracker = Callable[[Callable[..., object]], Callable[..., object]]


@pytest.fixture
def metrics_disabled() -> object:
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
    """Return fakeredis state for deterministic Redis API and state tests."""
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
        selections=[AllSelection()],
    )


@pytest.fixture
def test_settings(
    monkeypatch: pytest.MonkeyPatch,
    redis: Redis,
    schema_writers: SchemaWriters,
    sync_config_path: Path,
) -> Settings:
    """Provide settings configured with test dependency objects."""

    def redis_client(_settings: Settings, db: int | None = None) -> Redis:
        return redis

    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", sync_config_path)
    monkeypatch.setattr(Settings, "redis", redis_client)
    monkeypatch.setattr(settings, "SCHEMA_WRITERS", schema_writers)
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
            render_template(
                "bigquery/table_metadata",
                {"table_name": name},
                root=FILES.parent / "sql",
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
            render_template("bigquery/partitions", {}, root=FILES.parent / "sql"),
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
def container_network() -> Iterator[Network]:
    """Provide a network on which the containers reach each other by name."""
    with Network() as network:
        yield network


@pytest.fixture(scope="session")
def seaweedfs(
    container_network: Network,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SeaweedFS]:
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
        yield SeaweedFS(
            container.get_container_host_ip(),
            container.get_exposed_port(9000),
            client,
        )
    finally:
        container.stop()
