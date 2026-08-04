"""Tests for dp.sync.finalizer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection

from dp.constants import SYNC_FINALIZE_STREAM
from dp.settings import settings
from dp.sync.finalizer import (
    broker,
    gcs_paths_for_table,
    load_table,
    partition_value_from_path,
)
from dp.sync.models import (
    FinalizeMessage,
    Strategy,
)


class TestPartitionValueFromPath:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("gs://b/t/2025-01-15/data.parquet", "2025-01-15"),
            ("gs://b/t/2025-01/data.parquet", "2025-01"),
            ("gs://b/prefix/table/abc123/data.parquet", "abc123"),
        ],
        ids=["iso-date", "month", "arbitrary-segment"],
    )
    def test_extracts_partition_value(self, path: str, expected: str) -> None:
        """Second-to-last path segment is returned as the partition value."""
        assert partition_value_from_path(path) == expected


class TestGcsPathsForTable:
    def test_returns_paths_from_glob(self) -> None:
        """Paths returned match the rows produced by the DuckDB glob query."""
        db = FakeDuckDBConnection(
            rows=[
                ("gs://b/mytable/2025-01-15/data.parquet",),
                ("gs://b/mytable/2025-01-14/data.parquet",),
            ]
        )
        with (
            patch("dp.sync.finalizer.connect", return_value=db),
            patch("dp.sync.finalizer.load_template", return_value="SELECT 1"),
        ):
            paths = gcs_paths_for_table("mytable")

        assert len(paths) == 2
        assert paths[0] == "gs://b/mytable/2025-01-15/data.parquet"

    def test_returns_empty_when_no_files(self) -> None:
        """Empty list is returned when DuckDB glob finds no matching Parquet files."""
        db = FakeDuckDBConnection()
        with (
            patch("dp.sync.finalizer.connect", return_value=db),
            patch("dp.sync.finalizer.load_template", return_value="SELECT 1"),
        ):
            paths = gcs_paths_for_table("mytable")

        assert paths == []


class TestLoadTable:
    @pytest.mark.parametrize(
        ("strategy", "paths", "partition_column", "expected_calls"),
        [
            (Strategy.DUMP, ["gs://b/t/data.parquet"], None, 1),
            (
                Strategy.WINDOW,
                [
                    "gs://b/t/2025-01-15/data.parquet",
                    "gs://b/t/2025-01-14/data.parquet",
                ],
                "dt",
                2,
            ),
            (Strategy.DUMP, [], None, 0),
        ],
        ids=["dump", "window", "no-paths"],
    )
    def test_load_table(
        self,
        strategy: Strategy,
        paths: list[str],
        partition_column: str | None,
        expected_calls: int,
    ) -> None:
        """load_template is called once per Parquet path; skipped entirely when none exist."""
        mock_load = MagicMock(return_value="DELETE FROM ...")
        with (
            patch("dp.sync.finalizer.gcs_paths_for_table", return_value=paths),
            patch("dp.sync.finalizer.load_template", mock_load),
        ):
            load_table(FakeDuckDBConnection(), "t", strategy, partition_column)

        assert mock_load.call_count == expected_calls


class TestFinalizeSync:
    @pytest.mark.asyncio
    async def test_processes_dump_table_and_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dump table is loaded and finalizer exits after receiving a finalize message."""
        cfg = tmp_path / "sync.json"
        cfg.write_text('{"tables": [{"bq_table": "p.d.t", "strategy": "dump"}]}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        db = FakeDuckDBConnection()
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect", return_value=db),
                patch("dp.sync.finalizer.load_table") as mock_load,
                patch("dp.sync.finalizer.finalizer.exit") as mock_exit,
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )
        assert len(db.executed) >= 1
        mock_load.assert_called_once()
        mock_exit.assert_called_once()

    @pytest.mark.asyncio
    async def test_processes_window_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Window table is loaded once after receiving a finalize message."""
        cfg = tmp_path / "sync.json"
        cfg.write_text(
            '{"tables": [{"bq_table": "p.d.t", "strategy": "window", "partition": {"column": "dt", "type": "DAY", "n": 7}}]}'
        )
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect", return_value=FakeDuckDBConnection()),
                patch("dp.sync.finalizer.load_table") as mock_load,
                patch("dp.sync.finalizer.finalizer.exit"),
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )
        mock_load.assert_called_once()
