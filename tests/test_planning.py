"""Tests for synchronization planning operations."""

from hashlib import sha256
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from google.cloud import bigquery
from helpers import FakeDuckDBConnection, FakeRedis
from redis.asyncio import Redis

from dp.constants import SYNC_STATE_KEY
from dp.models import DumpTable, PartitionConfig, SyncConfig, WindowTable
from dp.planning import (
    detect_changes,
    discover_json_columns,
    discover_partitions,
    expand_config,
    plan_sync,
    table_signature,
)


class FakeBigQueryClient:
    """Minimal metadata client used to verify reuse and closure."""

    close_calls: int

    def __init__(self) -> None:
        """Initialize close-call tracking."""
        self.close_calls = 0

    def close(self) -> None:
        """Record one close operation."""
        self.close_calls += 1


def redis_client(fake: FakeRedis) -> Redis:
    """Cast the Valkey test double to the production Redis type."""
    return cast(Redis, cast(object, fake))


def test_discovers_struct_columns() -> None:
    """STRUCT columns are selected for JSON conversion."""
    db = FakeDuckDBConnection(
        describe_rows=[
            ("cpf", "VARCHAR", None, None, None, None),
            ("units", "STRUCT(id VARCHAR)[]", None, None, None, None),
        ]
    )

    assert discover_json_columns(db, "p.d.t") == ["units"]


def test_discovers_last_window_values() -> None:
    """Only the configured latest window values are returned."""
    db = FakeDuckDBConnection(rows=[("2025-01-13",), ("2025-01-15",), ("2025-01-14",)])
    table = WindowTable(
        bq_table="p.d.t",
        partition=PartitionConfig(column="dt", n=2),
    )

    result = discover_partitions(db, table)

    assert result == ["2025-01-15", "2025-01-14"]


def test_expands_dump_and_window_tables() -> None:
    """Dump and window tables retain their existing extraction behavior."""
    config = SyncConfig(
        tables=[
            DumpTable(bq_table="p.d.dump"),
            WindowTable(
                bq_table="p.d.window",
                partition=PartitionConfig(column="dt", n=2),
            ),
        ]
    )
    with patch(
        "dp.planning.discover_partitions",
        return_value=["2025-01-15", "2025-01-14"],
    ):
        tasks = expand_config(config, "bucket", "sync-1", FakeDuckDBConnection())

    assert [task.gcs_path for task in tasks] == [
        "s3://bucket/d/dump/data.parquet",
        "s3://bucket/d/window/2025-01-15/data.parquet",
        "s3://bucket/d/window/2025-01-14/data.parquet",
    ]


def test_signature_includes_sync_configuration() -> None:
    """Configuration changes invalidate a stored source timestamp."""
    table = DumpTable(bq_table="p.d.t", pg_schema="other")
    expected_hash = sha256(table.model_dump_json().encode()).hexdigest()

    assert table_signature(table, "100") == f"100:{expected_hash}"


@pytest.mark.asyncio
async def test_detects_only_changed_tables_and_reuses_client() -> None:
    """One metadata client per project serves all configured tables."""
    config = SyncConfig(
        tables=[DumpTable(bq_table="p.d.t1"), DumpTable(bq_table="p.d.t2")]
    )
    fake_redis = FakeRedis()
    fake_redis.store[SYNC_STATE_KEY.format(bq_table="p.d.t1")] = table_signature(
        config.tables[0], "100"
    )
    client = FakeBigQueryClient()

    with (
        patch(
            "dp.planning.bigquery.Client",
            return_value=cast(bigquery.Client, cast(object, client)),
        ),
        patch("dp.planning.table_modified", side_effect=["100", "200"]),
    ):
        changed = await detect_changes(config, redis_client(fake_redis))

    assert changed == {"p.d.t2": table_signature(config.tables[1], "200")}
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_builds_plan_with_exact_parquet_paths() -> None:
    """The plan contains only tables that produced extraction tasks."""
    config = SyncConfig(tables=[DumpTable(bq_table="p.d.t")])
    fake_redis = FakeRedis()

    with patch(
        "dp.planning.detect_changes",
        new_callable=AsyncMock,
        return_value={"p.d.t": "signature"},
    ):
        plan, tasks = await plan_sync(
            config,
            redis_client(fake_redis),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert plan is not None
    assert plan.signatures == {"p.d.t": "signature"}
    assert plan.paths == {"p.d.t": ["s3://bucket/d/t/data.parquet"]}
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_returns_no_plan_when_nothing_changed() -> None:
    """An unchanged run creates no finalizer plan or tasks."""
    config = SyncConfig(tables=[DumpTable(bq_table="p.d.t")])

    with patch(
        "dp.planning.detect_changes",
        new_callable=AsyncMock,
        return_value={},
    ):
        plan, tasks = await plan_sync(
            config,
            redis_client(FakeRedis()),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert plan is None
    assert tasks == []
