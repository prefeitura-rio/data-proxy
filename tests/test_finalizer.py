"""Tests for dp.sync.finalizer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from faststream.redis.testing import TestRedisBroker

from dp.constants import SYNC_FINALIZE_STREAM
from dp.settings import settings
from dp.sync.finalizer import (
    bootstrap_table,
    broker,
    load_table,
)
from dp.sync.models import (
    FinalizeMessage,
    RlsConfig,
    Strategy,
)


class TestBootstrapTable:
    def test_creates_table_and_grants_without_rls(self) -> None:
        """Grant is applied; RLS steps skipped when rls is None."""
        pg_conn = MagicMock()
        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            bootstrap_table(
                pg_conn,
                {
                    "schema": "pic",
                    "table_name": "mytable",
                    "gcs_path": "gs://b/t/data.parquet",
                    "rls": None,
                },
            )

        # 1 execute call (grant_select)
        assert pg_conn.execute.call_count == 1

    def test_enables_rls_when_configured(self) -> None:
        """Grant + RLS are applied when rls config is present."""
        pg_conn = MagicMock()
        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            bootstrap_table(
                pg_conn,
                {
                    "schema": "pic",
                    "table_name": "mytable",
                    "gcs_path": "gs://b/t/data.parquet",
                    "rls": RlsConfig(column="id_unidade"),
                },
            )

        # 1 execute call (grant_select + enable_rls joined)
        assert pg_conn.execute.call_count == 1


class TestLoadTable:
    def test_dump_loads_once(self) -> None:
        """Dump strategy calls delete + insert once."""
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [("gs://b/t/data.parquet",)]

        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            load_table(db, "t", Strategy.DUMP, None)

        assert db.execute.call_count == 3  # glob + delete + insert

    def test_window_loads_per_partition(self) -> None:
        """Window strategy calls delete + insert per partition."""
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            ("gs://b/t/2025-01-15/data.parquet",),
            ("gs://b/t/2025-01-14/data.parquet",),
        ]

        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            load_table(db, "t", Strategy.WINDOW, "dt")

        assert db.execute.call_count == 5  # glob + 2*(delete+insert)

    def test_skips_when_no_paths(self) -> None:
        """No Parquet found → skip, no load calls."""
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []

        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            load_table(db, "t", Strategy.DUMP, None)

        assert db.execute.call_count == 1  # only glob


class TestFinalizeSync:
    @pytest.mark.asyncio
    async def test_processes_dump_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dump table is created and loaded."""
        cfg = tmp_path / "sync.json"
        cfg.write_text('{"tables": [{"bq_table": "p.d.t", "strategy": "dump"}]}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect") as mock_ddb,
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.bootstrap_table") as mock_bootstrap,
                patch("dp.sync.finalizer.load_table") as mock_load,
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )
        mock_ddb.assert_called_once()
        mock_pg.assert_called_once()
        mock_bootstrap.assert_called_once()
        mock_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_processes_window_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Window table is created and loaded."""
        cfg = tmp_path / "sync.json"
        cfg.write_text(
            '{"tables": [{"bq_table": "p.d.t", "strategy": "window", "partition": {"column": "dt", "type": "DAY", "n": 7}}]}'
        )
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect") as mock_ddb,
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.bootstrap_table") as mock_bootstrap,
                patch("dp.sync.finalizer.load_table") as mock_load,
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )
        mock_ddb.assert_called_once()
        mock_pg.assert_called_once()
        mock_bootstrap.assert_called_once()
        mock_load.assert_called_once()
