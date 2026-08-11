"""Tests for dp.sync.finalizer."""

from pathlib import Path
from typing import cast
from unittest.mock import patch

import psycopg
import pytest
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection, FakePgConn

from dp.constants import SYNC_FINALIZE_STREAM
from dp.settings import settings
from dp.sync.finalizer import (
    bootstrap_table,
    broker,
    finalizer,
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
        pg_conn = FakePgConn()
        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            bootstrap_table(
                cast("psycopg.Connection[tuple[object, ...]]", cast(object, pg_conn)),
                {
                    "schema": "pic",
                    "table_name": "mytable",
                    "gcs_path": "gs://b/t/data.parquet",
                    "rls": None,
                },
            )

        # 1 execute call (grant_select)
        assert pg_conn.execute_calls == 1

    def test_enables_rls_when_configured(self) -> None:
        """Grant + RLS are applied when rls config is present."""
        pg_conn = FakePgConn()
        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            bootstrap_table(
                cast("psycopg.Connection[tuple[object, ...]]", cast(object, pg_conn)),
                {
                    "schema": "pic",
                    "table_name": "mytable",
                    "gcs_path": "gs://b/t/data.parquet",
                    "rls": RlsConfig(column="id_unidade"),
                },
            )

        # 1 execute call (grant_select + enable_rls joined)
        assert pg_conn.execute_calls == 1


class TestLoadTable:
    def test_dump_loads_once(self) -> None:
        """Dump strategy calls delete + insert once."""
        db = FakeDuckDBConnection(rows=[("gs://b/t/data.parquet",)])

        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            load_table(db, "pic", "t", Strategy.DUMP, None)

        assert len(db.executed) == 3  # glob + delete + insert

    def test_window_loads_per_partition(self) -> None:
        """Window strategy calls delete + insert per partition."""
        db = FakeDuckDBConnection(
            rows=[
                ("gs://b/t/2025-01-15/data.parquet",),
                ("gs://b/t/2025-01-14/data.parquet",),
            ]
        )

        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            load_table(db, "pic", "t", Strategy.WINDOW, "dt")

        assert len(db.executed) == 5  # glob + 2*(delete+insert)

    def test_skips_when_no_paths(self) -> None:
        """No Parquet found → skip, no load calls."""
        db = FakeDuckDBConnection(rows=[])

        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            load_table(db, "pic", "t", Strategy.DUMP, None)

        assert len(db.executed) == 1  # only glob


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
        assert (
            mock_pg.call_count == 2
        )  # once for init_schema, once for notify+bootstrap
        mock_bootstrap.assert_called_once()
        mock_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_processes_window_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Window table is created and loaded."""
        cfg = tmp_path / "sync.json"
        cfg.write_text(
            '{"tables": [{"bq_table": "p.d.t", "strategy": "window", "partition": {"column": "dt", "n": 7}}]}'
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
        assert (
            mock_pg.call_count == 2
        )  # once for init_schema, once for notify+bootstrap
        mock_bootstrap.assert_called_once()
        mock_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_indexes_when_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Third psycopg connection (autocommit=True) is opened when indexes are declared."""
        cfg = tmp_path / "sync.json"
        cfg.write_text(
            '{"tables": [{"bq_table": "p.d.t", "strategy": "dump", "indexes": [{"name": "idx_t_col", "columns": ["col"]}]}]}'
        )
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect"),
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.bootstrap_table"),
                patch("dp.sync.finalizer.load_table"),
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )
        assert mock_pg.call_count == 3  # init_schema + notify/bootstrap + indexes
        assert mock_pg.call_args_list[2].kwargs.get("autocommit") is True

    @pytest.mark.asyncio
    async def test_window_table_calls_create_table_when_paths_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_table is called when GCS paths are found for a window table."""
        cfg = tmp_path / "sync.json"
        cfg.write_text(
            '{"tables": [{"bq_table": "p.d.t", "strategy": "window", "partition": {"column": "dt", "n": 7}}]}'
        )
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        fake_ddb = FakeDuckDBConnection(rows=[("gs://b/t/2025-01-15/data.parquet",)])
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect", return_value=fake_ddb),
                patch("dp.sync.finalizer.psycopg.connect"),
                patch("dp.sync.finalizer.bootstrap_table"),
                patch("dp.sync.finalizer.load_table"),
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )
        # attach_postgres + list_parquets + create_table = 3 execute calls
        assert len(fake_ddb.executed) == 3

    @pytest.mark.asyncio
    async def test_skips_index_connection_when_no_indexes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No third psycopg connection when no table declares indexes."""
        cfg = tmp_path / "sync.json"
        cfg.write_text('{"tables": [{"bq_table": "p.d.t", "strategy": "dump"}]}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect"),
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.bootstrap_table"),
                patch("dp.sync.finalizer.load_table"),
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )
        assert mock_pg.call_count == 2  # no index connection opened

    @pytest.mark.asyncio
    async def test_exits_after_finalization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """finalizer.exit() is called after finalization completes."""
        cfg = tmp_path / "sync.json"
        cfg.write_text('{"tables": [{"bq_table": "p.d.t", "strategy": "dump"}]}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect"),
                patch("dp.sync.finalizer.psycopg.connect"),
                patch("dp.sync.finalizer.bootstrap_table"),
                patch("dp.sync.finalizer.load_table"),
                patch.object(finalizer, "exit") as mock_exit,
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )
        mock_exit.assert_called_once()
