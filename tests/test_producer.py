"""Tests for dp.sync.producer."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from faststream import TestApp
from helpers import FakeDuckDBConnection, FakeRedis, FakeRedisCM

from dp.settings import settings
from dp.sync.models import (
    DumpTable,
    PartitionConfig,
    SyncConfig,
    WindowTable,
)
from dp.sync.producer import discover_partitions, expand_config, producer


class TestDiscoverPartitions:
    def test_returns_partition_values(self) -> None:
        """Partition values are extracted and returned in descending order."""
        db = FakeDuckDBConnection(rows=[("2025-01-14",), ("2025-01-15",)])
        table = WindowTable(
            bq_table="p.d.t",
            partition=PartitionConfig(column="dt", n=2),
        )
        result = discover_partitions(db, table)

        assert result == ["2025-01-15", "2025-01-14"]
        assert len(db.executed) == 1
        assert "ORDER BY" not in db.executed[0]
        assert "LIMIT" not in db.executed[0]

    def test_slices_to_n_most_recent_partitions(self) -> None:
        """Only the n most-recent partition values are returned."""
        db = FakeDuckDBConnection(
            rows=[("2025-01-13",), ("2025-01-15",), ("2025-01-14",)]
        )
        table = WindowTable(
            bq_table="p.d.t",
            partition=PartitionConfig(column="dt", n=2),
        )
        result = discover_partitions(db, table)

        assert result == ["2025-01-15", "2025-01-14"]

    def test_returns_empty_when_no_partitions(self) -> None:
        """Empty list is returned when no partition rows exist in BigQuery."""
        db = FakeDuckDBConnection()
        table = WindowTable(
            bq_table="p.d.t",
            partition=PartitionConfig(column="dt", n=7),
        )
        with patch(
            "dp.sync.producer.load_template", return_value="SELECT DISTINCT ..."
        ):
            result = discover_partitions(db, table)

        assert result == []


class TestExpandConfig:
    def test_dump_yields_one_task(self) -> None:
        """A dump table produces exactly one SyncTask with no partition fields."""
        config = SyncConfig(tables=[DumpTable(bq_table="p.d.t")])
        with patch("dp.sync.producer.connect", return_value=FakeDuckDBConnection()):
            tasks = list(expand_config(config, "my-bucket", "sync-1"))

        assert len(tasks) == 1
        assert tasks[0].gcs_path == "s3://my-bucket/t/data.parquet"
        assert tasks[0].partition_column is None

    def test_window_yields_tasks_for_each_partition(self) -> None:
        """A window table produces one SyncTask per discovered partition value."""
        config = SyncConfig(
            tables=[
                WindowTable(
                    bq_table="p.d.t",
                    partition=PartitionConfig(column="dt", n=2),
                )
            ]
        )
        with (
            patch("dp.sync.producer.connect", return_value=FakeDuckDBConnection()),
            patch(
                "dp.sync.producer.discover_partitions",
                return_value=["2025-01-15", "2025-01-14"],
            ),
        ):
            tasks = list(expand_config(config, "my-bucket", "sync-1"))

        assert len(tasks) == 2
        assert tasks[0].partition_value == "2025-01-15"
        assert tasks[1].partition_value == "2025-01-14"


class TestPublishTasks:
    @pytest.mark.asyncio
    async def test_publishes_tasks_and_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One message is published per expanded task and the app exits on completion."""
        cfg = tmp_path / "sync.json"
        cfg.write_text('{"tables": [{"bq_table": "p.d.t", "strategy": "dump"}]}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        with (
            patch("dp.sync.producer.connect", return_value=FakeDuckDBConnection()),
            patch(
                "dp.sync.producer.broker.publish", new_callable=AsyncMock
            ) as mock_pub,
            patch(
                "dp.settings.Settings.make_redis",
                return_value=FakeRedisCM(FakeRedis()),
            ),
            patch("dp.sync.producer.producer.exit"),
        ):
            async with TestApp(producer):
                pass

        mock_pub.assert_called_once()
