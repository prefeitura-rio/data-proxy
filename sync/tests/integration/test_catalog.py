from pathlib import Path
from unittest.mock import AsyncMock
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
    commit_table,
    current_snapshot_id,
    publish_schema,
)
from sync.models import (
    FullTable,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
)
from sync.settings import settings
from tests.constants import PARQUET, PARQUET_20
from tests.helpers import ducklake_row_count

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


@pytest.mark.usefixtures("use_catalog_paths")
class TestPublishSchema:
    """publish_schema writes to the real catalog database."""

    @pytest.fixture
    def use_catalog_paths(
        self, catalog_paths: DuckLakePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Send every schema to the catalog of this test."""

        def for_schema(_: str) -> DuckLakePaths:
            return catalog_paths

        monkeypatch.setattr(DuckLakePaths, "for_schema", staticmethod(for_schema))

    @staticmethod
    def one_table_plan() -> tuple[SyncConfig, SyncPlan]:
        """Build a config and a plan that publish one full table."""
        table = FullTable(name="p.d.people", resolved_schema="app")
        config = SyncConfig(schemas={"app": SchemaConfig(tables=[table])})
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.d.people": "sig"},
            paths={"p.d.people": [PARQUET]},
        )
        return config, plan

    async def test_publishes_full_table_into_catalog(self, duckdb: DuckDB) -> None:
        """Create a DuckLake table and insert rows from a full-table plan."""
        config, plan = self.one_table_plan()

        result = await publish_schema(duckdb, AsyncMock(), config, plan, set())

        assert result.published_tables == {"p.d.people"}
        assert await ducklake_row_count(duckdb, "people") == 10

    async def test_returns_committed_snapshot_id(self, duckdb: DuckDB) -> None:
        """Return the snapshot that holds the published tables."""
        config, plan = self.one_table_plan()

        result = await publish_schema(duckdb, AsyncMock(), config, plan, set())

        rows = await duckdb.query("SELECT id FROM dl.current_snapshot()")
        assert result.snapshot_id == rows[0][0]

    async def test_returns_no_snapshot_when_nothing_is_published(
        self, duckdb: DuckDB
    ) -> None:
        """Return no snapshot when every table is blocked by a failed path."""
        config, plan = self.one_table_plan()

        result = await publish_schema(duckdb, AsyncMock(), config, plan, {PARQUET})

        assert result.published_tables == set()
        assert result.snapshot_id is None


class TestDucklakeCommitTable:
    """DuckLake commit_table behavior against a real catalog."""

    async def test_inserts_and_replaces_rows(
        self, duckdb: DuckDB, catalog_paths: DuckLakePaths
    ) -> None:
        """Insert rows from Parquet, then replace them with a new file."""
        await attach_catalog(duckdb, catalog_paths, encrypted=False)
        table = FullTable(name="p.d.people", resolved_schema="app")

        await commit_table(duckdb, table, [PARQUET], None)
        assert await ducklake_row_count(duckdb, "people") == 10

        await commit_table(duckdb, table, [PARQUET_20], None)
        assert await ducklake_row_count(duckdb, "people") == 10
