import sqlite3
import stat
from pathlib import Path
from unittest.mock import AsyncMock

import duckdb
import psycopg
import pytest

from data_proxy.ducklake import DuckLakePaths, reader_snapshot, wait_for_reader
from data_proxy.postgres import Postgres
from tests.fixtures.types import FakeReader


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


class TestWaitForReader:
    """wait_for_reader behavior tests."""

    @pytest.mark.parametrize(
        ("snapshots", "expected_sleeps"),
        [
            pytest.param([5], 0, id="reader-already-at-snapshot"),
            pytest.param([9], 0, id="reader-ahead-of-snapshot"),
            pytest.param([3, 4, 5], 2, id="reader-catches-up-after-polls"),
            pytest.param([None, None, 5], 2, id="catalog-missing-then-ready"),
        ],
    )
    async def test_returns_once_reader_reaches_snapshot(
        self, reader: FakeReader, snapshots: list[int | None], expected_sleeps: int
    ) -> None:
        reader.snapshots = snapshots

        await wait_for_reader(
            reader.read,
            5,
            timeout=30,
            interval=1,
            sleep=reader.sleep,
            clock=reader.clock,
        )

        assert len(reader.sleeps) == expected_sleeps

    @pytest.mark.parametrize(
        "snapshots",
        [
            pytest.param([3], id="reader-stays-behind"),
            pytest.param([None], id="catalog-stays-missing"),
        ],
    )
    async def test_raises_when_reader_never_reaches_snapshot(
        self, reader: FakeReader, snapshots: list[int | None]
    ) -> None:
        reader.snapshots = snapshots

        with pytest.raises(TimeoutError):
            await wait_for_reader(
                reader.read,
                5,
                timeout=3,
                interval=1,
                sleep=reader.sleep,
                clock=reader.clock,
            )

        assert reader.polls >= 3


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
