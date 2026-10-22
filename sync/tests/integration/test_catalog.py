from pathlib import Path
from uuid import uuid4

import duckdb
import psycopg
import pytest
from psycopg.sql import SQL, Identifier

from sync.duckdb import DuckDB
from sync.ducklake import (
    DuckLakePaths,
    apply_maintenance,
    attach_catalog,
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
