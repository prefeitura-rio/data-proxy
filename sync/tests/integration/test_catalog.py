from pathlib import Path
from uuid import uuid4

import duckdb
import psycopg
import pytest
from psycopg.sql import SQL, Identifier

from sync.duckdb import DuckDB, limits_sql
from sync.ducklake import (
    DuckLakePaths,
    apply_maintenance,
    attach_catalog,
    configure_catalog,
    current_snapshot_id,
)
from sync.settings import settings

pytestmark = pytest.mark.postgres


async def test_each_schema_writes_to_its_own_metadata_schema(tmp_path: Path) -> None:
    """Keep the metadata of two schemas apart in one catalog database."""
    names = [f"catalog_{uuid4().hex[:8]}" for _ in range(2)]
    connection = duckdb.connect()
    duckdb_conn = DuckDB(connection=connection)
    catalog = await psycopg.AsyncConnection.connect(
        settings.DUCKLAKE_CATALOG_URL, autocommit=True
    )
    try:
        for index, name in enumerate(names):
            paths = DuckLakePaths(
                catalog=f"ducklake:postgres:{settings.DUCKLAKE_CATALOG_URL}",
                data=str(tmp_path / name),
                metadata_schema=name,
            )
            await attach_catalog(duckdb_conn, paths, encrypted=False)
            connection.execute(f"CREATE TABLE dl.items AS SELECT {index} AS id")
            connection.execute("DETACH dl")

        found = await catalog.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(%s) ORDER BY 1",
            [names],
        )
        assert [row[0] for row in await found.fetchall()] == sorted(names)
        for name in names:
            tables = await catalog.execute(
                SQL(
                    "SELECT count(*) FROM {}.ducklake_table WHERE table_name = 'items'"
                ).format(Identifier(name))
            )
            assert await tables.fetchone() == (1,)
    finally:
        for name in names:
            await catalog.execute(
                SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(Identifier(name))
            )
        await catalog.close()


async def test_configure_catalog_sets_small_row_groups(
    duckdb: DuckDB, catalog_paths: DuckLakePaths
) -> None:
    """DuckLake writes the rows of a table in row groups, and a table with wide JSON columns needs more than 3 GB for the default of 122880 rows."""
    await configure_catalog(duckdb, catalog_paths, encrypted=False)

    rows = await duckdb.query(
        "SELECT value FROM dl.options() WHERE option_name = 'parquet_row_group_size'"
    )

    assert rows == [("10000",)]


async def test_wide_rows_are_written_in_row_groups_of_limited_size(
    duckdb: DuckDB, catalog_paths: DuckLakePaths, tmp_path: Path
) -> None:
    """A row group is in memory while DuckDB writes it, so 10000 rows of 16 KB each need more than 1 GB. 8000 rows of 10 KB stay under the rows limit and go over the bytes limit."""
    duckdb.connection.execute(limits_sql(tmp_path / "spill"))
    await configure_catalog(duckdb, catalog_paths, encrypted=False)
    duckdb.connection.execute("CREATE TABLE dl.wide (id INTEGER, body VARCHAR)")
    duckdb.connection.execute(
        "INSERT INTO dl.wide SELECT i, repeat('x', 10000) || i FROM range(8000) t(i)"
    )

    rows = await duckdb.query(
        f"""
        SELECT count(DISTINCT row_group_id)
        FROM parquet_metadata('{catalog_paths.data}/**/*.parquet')
        """
    )

    groups = rows[0][0]
    assert isinstance(groups, int)
    assert groups > 1


class TestApplyMaintenance:
    """Maintenance runs on a real catalog and keeps the table readable."""

    @pytest.mark.parametrize(
        "expiration",
        [pytest.param("7d", id="default"), pytest.param("30d", id="thirty-days")],
    )
    async def test_keeps_the_rows_and_returns_the_current_snapshot(
        self,
        duckdb: DuckDB,
        catalog_paths: DuckLakePaths,
        monkeypatch: pytest.MonkeyPatch,
        expiration: str,
    ) -> None:
        """Keeps the rows and returns the current snapshot."""
        monkeypatch.setattr(settings, "DUCKLAKE_SNAPSHOT_EXPIRATION", expiration)
        await attach_catalog(duckdb, catalog_paths, encrypted=False)
        duckdb.connection.execute("CREATE TABLE dl.items (id INTEGER)")
        duckdb.connection.execute("INSERT INTO dl.items VALUES (1)")
        duckdb.connection.execute("INSERT INTO dl.items VALUES (2)")
        duckdb.connection.execute("DETACH dl")

        snapshot_id = await apply_maintenance(duckdb, catalog_paths, encrypted=False)

        assert snapshot_id == await current_snapshot_id(duckdb)
        rows = duckdb.connection.execute("SELECT id FROM dl.items ORDER BY id")
        assert rows.fetchall() == [(1,), (2,)]
