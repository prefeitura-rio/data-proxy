import sqlite3
import stat
from pathlib import Path
from unittest.mock import AsyncMock

import duckdb
import psycopg
import pytest

from data_proxy.duckdb import DuckDB
from data_proxy.ducklake import (
    DuckLakePaths,
    apply_maintenance,
    attach_catalog,
    current_snapshot_id,
    reader_snapshot,
)
from data_proxy.postgres import Postgres
from data_proxy.settings import settings


def test_catalog_format_matches_pg_duckdb_support(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.sqlite"
    connection = duckdb.connect()
    connection.execute("LOAD ducklake")
    options = f"DATA_PATH '{tmp_path / 'data'}', DATA_INLINING_ROW_LIMIT 0"
    connection.execute(f"ATTACH 'ducklake:sqlite:{catalog}' AS dl ({options})")

    version = (
        sqlite3.connect(catalog)
        .execute("SELECT value FROM ducklake_metadata WHERE key = 'version'")
        .fetchone()[0]
    )

    assert version == "1.0"


def test_catalog_directory_allows_postgres_to_read(tmp_path: Path) -> None:
    paths = DuckLakePaths(
        catalog=tmp_path / "schema" / "catalog.sqlite",
        data="s3://bucket/ducklake/schema",
    )

    paths.prepare_catalog_directory()

    assert stat.S_IMODE(paths.catalog.parent.stat().st_mode) == 0o755


def test_catalog_directory_permissions_are_fixed_on_existing_path(
    tmp_path: Path,
) -> None:
    catalog_dir = tmp_path / "schema"
    catalog_dir.mkdir(mode=0o700)
    catalog_dir.chmod(0o700)
    paths = DuckLakePaths(
        catalog=catalog_dir / "catalog.sqlite",
        data="s3://bucket/ducklake/schema",
    )

    paths.prepare_catalog_directory()

    assert stat.S_IMODE(catalog_dir.stat().st_mode) == 0o755


class TestApplyMaintenance:
    """Maintenance runs on a real catalog and keeps the table readable."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "expiration",
        [pytest.param("7d", id="default"), pytest.param("30d", id="thirty-days")],
    )
    async def test_keeps_the_rows_and_returns_the_current_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expiration: str
    ) -> None:
        monkeypatch.setattr(settings, "DUCKLAKE_SNAPSHOT_EXPIRATION", expiration)
        connection = duckdb.connect()
        connection.execute("LOAD ducklake")
        duckdb_conn = DuckDB(connection=connection)
        paths = DuckLakePaths(
            catalog=tmp_path / "schema" / "catalog.sqlite",
            data=str(tmp_path / "data"),
        )
        paths.prepare_catalog_directory()
        await attach_catalog(duckdb_conn, paths, encrypted=False)
        connection.execute("CREATE TABLE dl.items (id INTEGER)")
        connection.execute("INSERT INTO dl.items VALUES (1)")
        connection.execute("INSERT INTO dl.items VALUES (2)")
        connection.execute("DETACH dl")

        snapshot_id = await apply_maintenance(duckdb_conn, paths, encrypted=False)

        assert snapshot_id == await current_snapshot_id(duckdb_conn)
        assert connection.execute("SELECT id FROM dl.items ORDER BY id").fetchall() == [
            (1,),
            (2,),
        ]


class TestReaderSnapshot:
    """reader_snapshot behavior tests."""

    async def test_returns_snapshot_from_reader_function(self) -> None:
        pg_conn = AsyncMock(spec=Postgres)
        pg_conn.query.return_value = [(7,)]

        assert await reader_snapshot(pg_conn, "app") == 7
        pg_conn.commit.assert_awaited_once()

    async def test_returns_none_when_reader_catalog_is_missing(self) -> None:
        pg_conn = AsyncMock(spec=Postgres)
        pg_conn.query.side_effect = psycopg.errors.InternalError_("no catalog")

        assert await reader_snapshot(pg_conn, "app") is None
        pg_conn.rollback.assert_awaited_once()
