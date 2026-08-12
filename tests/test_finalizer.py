"""Tests for dp.sync.finalizer."""

from pathlib import Path
from typing import cast
from unittest.mock import ANY, AsyncMock, patch

import psycopg
import pytest
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection, FakePgConn, FakeRedisCM, FakeRedisGroup
from redis.exceptions import ResponseError

from dp.constants import FINALIZERS_GROUP, SYNC_FINALIZE_STREAM
from dp.settings import settings
from dp.sync.finalizer import (
    bootstrap_table,
    broker,
    ensure_consumer_group,
    finalize_sync,
    finalizer,
    list_parquets,
    load_table,
    publish_table,
)
from dp.sync.models import (
    DumpTable,
    FinalizeMessage,
    IndexConfig,
    PartitionConfig,
    RlsConfig,
    WindowTable,
)
from dp.templates import load_template as render_template


class TestSqlTemplates:
    def test_init_roles_preserves_postgres_dollar_quotes(self) -> None:
        sql = render_template(
            "pg/init_roles",
            {
                "user_role": "web_user",
                "authenticator_role": "authenticator",
                "rls_schema": "rls",
            },
        )

        assert "DO $$" in sql
        assert "END\n$$;" in sql


class TestEnsureConsumerGroup:
    @pytest.mark.asyncio
    async def test_creates_group_at_id_zero(self) -> None:
        fake = FakeRedisGroup()

        with patch("dp.settings.Settings.make_redis", return_value=FakeRedisCM(fake)):
            await ensure_consumer_group()

        assert fake.calls == [
            {
                "name": SYNC_FINALIZE_STREAM,
                "groupname": FINALIZERS_GROUP,
                "id": "0",
                "mkstream": True,
            }
        ]

    @pytest.mark.asyncio
    async def test_handles_existing_group(self) -> None:
        fake = FakeRedisGroup(side_effect=ResponseError("BUSYGROUP ..."))

        with patch("dp.settings.Settings.make_redis", return_value=FakeRedisCM(fake)):
            await ensure_consumer_group()


class TestBootstrapTable:
    def test_creates_table_and_grants_without_rls(self) -> None:
        """Grant is applied; RLS steps skipped when rls is None."""
        pg_conn = FakePgConn()
        mappings: list[dict[str, str]] = []

        def capture_template(name: str, mapping: dict[str, str]) -> str:
            mappings.append(mapping)
            return "SELECT 1"

        with patch("dp.sync.finalizer.load_template", side_effect=capture_template):
            bootstrap_table(
                cast("psycopg.Connection[tuple[object, ...]]", cast(object, pg_conn)),
                {
                    "schema": "pic",
                    "table_name": "mytable",
                    "rls": None,
                },
            )

        assert pg_conn.execute_calls == 1
        assert mappings == [
            {"schema": "pic", "table": "mytable", "user_role": "web_user"}
        ]

    def test_enables_rls_when_configured(self) -> None:
        """Grant + RLS are applied when rls config is present."""
        pg_conn = FakePgConn()
        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            bootstrap_table(
                cast("psycopg.Connection[tuple[object, ...]]", cast(object, pg_conn)),
                {
                    "schema": "pic",
                    "table_name": "mytable",
                    "rls": RlsConfig(column="id_unidade"),
                },
            )

        # 1 execute call (grant_select + enable_rls joined)
        assert pg_conn.execute_calls == 1


class TestListParquets:
    def test_returns_all_table_paths(self) -> None:
        db = FakeDuckDBConnection(
            rows=[
                ("gs://b/t/2025-01-15/data.parquet",),
                ("gs://b/t/2025-01-14/data.parquet",),
            ]
        )

        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            paths = list_parquets(db, "t")

        assert paths == [
            "gs://b/t/2025-01-15/data.parquet",
            "gs://b/t/2025-01-14/data.parquet",
        ]
        assert len(db.executed) == 1


class TestLoadTable:
    def test_loads_every_path_into_shadow_table(self) -> None:
        db = FakeDuckDBConnection()
        mappings: list[dict[str, str]] = []
        paths = [
            "gs://b/t/2025-01-15/data.parquet",
            "gs://b/t/2025-01-14/data.parquet",
        ]

        def capture_template(name: str, mapping: dict[str, str]) -> str:
            mappings.append(mapping)
            return "SELECT 1"

        with patch("dp.sync.finalizer.load_template", side_effect=capture_template):
            load_table(db, "pic", "t__next", paths)

        assert len(db.executed) == 2
        assert [mapping["table_name"] for mapping in mappings] == [
            "t__next",
            "t__next",
        ]
        assert [mapping["gcs_path"] for mapping in mappings] == paths


class TestPublishTable:
    def test_prepares_swaps_and_indexes_in_order(self) -> None:
        pg_conn = FakePgConn()
        table = DumpTable(
            bq_table="p.pic.t",
            indexes=[IndexConfig(name="idx_t_col", columns=["col"])],
        )

        def template_name(name: str, mapping: dict[str, str]) -> str:
            return name

        with patch("dp.sync.finalizer.load_template", side_effect=template_name):
            publish_table(
                cast("psycopg.Connection[tuple[object, ...]]", cast(object, pg_conn)),
                table,
            )

        assert pg_conn.executed == [
            b"pg/grant_select",
            b"pg/swap_table",
            b"pg/create_index",
        ]

    def test_window_uses_the_same_publication_flow(self) -> None:
        pg_conn = FakePgConn()
        table = WindowTable(
            bq_table="p.pic.t",
            partition=PartitionConfig(column="dt", n=7),
        )

        with patch("dp.sync.finalizer.load_template", return_value="SELECT 1"):
            publish_table(
                cast("psycopg.Connection[tuple[object, ...]]", cast(object, pg_conn)),
                table,
            )

        assert pg_conn.execute_calls == 2  # bootstrap + swap

    def test_swap_supports_missing_current_table(self) -> None:
        sql = Path("src/dp/sql/pg/swap_table.sql").read_text()

        assert "ALTER TABLE IF EXISTS ${schema}.${table}" in sql


class TestFinalizeSync:
    @pytest.mark.asyncio
    async def test_processes_dump_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dump table is prepared and published through its shadow table."""
        cfg = tmp_path / "sync.json"
        cfg.write_text('{"tables": [{"bq_table": "p.d.t", "strategy": "dump"}]}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        path = "gs://b/t/data.parquet"
        fake_ddb = FakeDuckDBConnection(rows=[(path,)])
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect", return_value=fake_ddb) as mock_ddb,
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.bootstrap_table") as mock_bootstrap,
                patch("dp.sync.finalizer.load_table") as mock_load,
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )

        mock_ddb.assert_called_once()
        mock_pg.assert_called_once_with(settings.PG_DSN)
        mock_bootstrap.assert_called_once()
        mock_load.assert_called_once_with(ANY, "d", "t__next", [path])

    @pytest.mark.asyncio
    async def test_processes_window_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Window table loads every partition into one shadow table."""
        cfg = tmp_path / "sync.json"
        cfg.write_text(
            '{"tables": [{"bq_table": "p.d.t", "strategy": "window", "partition": {"column": "dt", "n": 7}}]}'
        )
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        paths = [
            "gs://b/t/2025-01-15/data.parquet",
            "gs://b/t/2025-01-14/data.parquet",
        ]
        fake_ddb = FakeDuckDBConnection(rows=[(path,) for path in paths])
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect", return_value=fake_ddb) as mock_ddb,
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.bootstrap_table") as mock_bootstrap,
                patch("dp.sync.finalizer.load_table") as mock_load,
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )

        mock_ddb.assert_called_once()
        mock_pg.assert_called_once_with(settings.PG_DSN)
        mock_bootstrap.assert_called_once()
        mock_load.assert_called_once_with(ANY, "d", "t__next", paths)

    @pytest.mark.asyncio
    async def test_creates_indexes_when_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Indexes are created in the publication transaction."""
        cfg = tmp_path / "sync.json"
        cfg.write_text(
            '{"tables": [{"bq_table": "p.d.t", "strategy": "dump", "indexes": [{"name": "idx_t_col", "columns": ["col"]}]}]}'
        )
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        fake_ddb = FakeDuckDBConnection(rows=[("gs://b/t/data.parquet",)])
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect", return_value=fake_ddb),
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.bootstrap_table"),
                patch("dp.sync.finalizer.load_table"),
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )

        mock_pg.assert_called_once_with(settings.PG_DSN)
        assert all("autocommit" not in call.kwargs for call in mock_pg.call_args_list)

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
    async def test_keeps_current_table_when_no_parquet_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "sync.json"
        cfg.write_text(
            '{"tables": [{"bq_table": "p.d.t", "strategy": "window", "partition": {"column": "dt", "n": 7}}]}'
        )
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        fake_ddb = FakeDuckDBConnection(rows=[])
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect", return_value=fake_ddb),
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.publish_table") as mock_publish,
                patch("dp.sync.finalizer.load_table") as mock_load,
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )

        mock_pg.assert_called_once_with(settings.PG_DSN)
        mock_publish.assert_not_called()
        mock_load.assert_not_called()
        assert len(fake_ddb.executed) == 2  # attach + list

    @pytest.mark.asyncio
    async def test_uses_publication_transaction_without_indexes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tables without indexes use the same publication transaction."""
        cfg = tmp_path / "sync.json"
        cfg.write_text('{"tables": [{"bq_table": "p.d.t", "strategy": "dump"}]}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        fake_ddb = FakeDuckDBConnection(rows=[("gs://b/t/data.parquet",)])
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.finalizer.connect", return_value=fake_ddb),
                patch("dp.sync.finalizer.psycopg.connect") as mock_pg,
                patch("dp.sync.finalizer.bootstrap_table"),
                patch("dp.sync.finalizer.load_table"),
            ):
                await br.publish(
                    FinalizeMessage(sync_id="s1"), stream=SYNC_FINALIZE_STREAM
                )

        mock_pg.assert_called_once_with(settings.PG_DSN)

    @pytest.mark.asyncio
    async def test_exits_after_finalization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "sync.json"
        cfg.write_text('{"tables": [{"bq_table": "p.d.t", "strategy": "dump"}]}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", cfg)
        with (
            patch("dp.sync.finalizer.connect"),
            patch("dp.sync.finalizer.psycopg.connect"),
            patch("dp.sync.finalizer.bootstrap_table"),
            patch("dp.sync.finalizer.load_table"),
            patch(
                "dp.sync.finalizer.broker.publish", new_callable=AsyncMock
            ) as mock_pub,
            patch.object(finalizer, "exit") as mock_exit,
        ):
            await finalize_sync(FinalizeMessage(sync_id="s1"))

        mock_pub.assert_awaited_once()
        mock_exit.assert_called_once()
