from pathlib import Path
from uuid import uuid4

import duckdb as duckdb_module
import psycopg
import pytest
from psycopg.sql import SQL, Identifier

from sync.duckdb import DuckDB, limits_sql
from sync.ducklake import (
    DuckLakePaths,
    apply_maintenance,
    attach_catalog,
    commit_table,
    configure_catalog,
    current_snapshot_id,
    publish_tables,
)
from sync.models import (
    FullTable,
    PartitionChange,
    PartitionedTablePlan,
    SyncPlan,
)
from sync.postgres import Postgres
from sync.settings import settings
from sync.state import read_table_signature
from tests.constants import PARQUET
from tests.helpers.ducklake import attach_ducklake, write_tagged_rows
from tests.helpers.state import partition, sync_config

pytestmark = pytest.mark.postgres


async def test_each_schema_writes_to_its_own_metadata_schema(tmp_path: Path) -> None:
    """Keep the metadata of two schemas apart in one catalog database."""
    names = [f"catalog_{uuid4().hex[:8]}" for _ in range(2)]
    connection = duckdb_module.connect()
    duckdb_conn = DuckDB(connection=connection)
    catalog = await psycopg.AsyncConnection.connect(
        settings.DUCKLAKE_CATALOG_URL.get_secret_value(), autocommit=True
    )
    try:
        for index, name in enumerate(names):
            paths = DuckLakePaths(
                catalog=f"ducklake:postgres:{settings.DUCKLAKE_CATALOG_URL.get_secret_value()}",
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


async def test_configure_catalog_sets_the_row_group_size(
    duckdb: DuckDB, catalog_paths: DuckLakePaths
) -> None:
    """DuckDB reads row groups of 100000 rows or more fastest, and the bytes limit bounds the memory of wide rows."""
    await configure_catalog(duckdb, catalog_paths, encrypted=False)

    rows = await duckdb.query(
        "SELECT value FROM dl.options() WHERE option_name = 'parquet_row_group_size'"
    )

    assert rows == [("122880",)]


async def test_wide_rows_are_written_in_row_groups_of_limited_size(
    duckdb: DuckDB, catalog_paths: DuckLakePaths, tmp_path: Path
) -> None:
    """A row group is in memory while DuckDB writes it, so 122880 rows of 10 KB each need more than 1 GB. 8000 rows of 10 KB stay under the rows limit and go over the bytes limit."""
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


async def test_a_failed_publish_keeps_the_state_of_the_tables_that_committed(
    duckdb: DuckDB, dbos_conn: psycopg.AsyncConnection, tmp_path: Path
) -> None:
    """Skip the committed tables in the next run: their signature stays although the third table fails."""
    names = ["p.d.a", "p.d.b", "p.d.c"]
    config = sync_config(
        [FullTable(name=name, resolved_schema="app") for name in names]
    )
    plan = SyncPlan(
        schema_name="app",
        signatures={name: f"sig-{name}" for name in names},
        paths={
            "p.d.a": [PARQUET],
            "p.d.b": [PARQUET],
            "p.d.c": [str(tmp_path / "missing.parquet")],
        },
    )
    pg_conn = Postgres(connection=dbos_conn)
    await attach_ducklake(duckdb, tmp_path)

    with pytest.raises(duckdb_module.Error):
        await publish_tables(duckdb, pg_conn, config, plan, set(names))

    assert [await read_table_signature(pg_conn, name) for name in names] == [
        "sig-p.d.a",
        "sig-p.d.b",
        None,
    ]


class TestBatchedPartitionCommit:
    """One file can hold several contiguous partitions, and a commit replaces only the partitions of the batches."""

    @pytest.mark.parametrize(
        ("batches", "expected_new"),
        [
            pytest.param(
                [["0", "10"]],
                list(range(20)),
                id="one-batch-of-contiguous-partitions",
            ),
            pytest.param(
                [["0"], ["20"]],
                [*range(10), *range(20, 30)],
                id="an-unchanged-partition-between-two-batches-keeps-its-rows",
            ),
        ],
    )
    async def test_replaces_only_the_rows_of_the_changed_partitions(
        self,
        duckdb: DuckDB,
        catalog_paths: DuckLakePaths,
        tmp_path: Path,
        batches: list[list[str]],
        expected_new: list[int],
    ) -> None:
        duckdb.connection.execute(limits_sql(tmp_path / "spill"))
        await configure_catalog(duckdb, catalog_paths, encrypted=False)
        table = FullTable(
            name="p.d.people", resolved_schema=catalog_paths.metadata_schema
        )
        physical = {pid: partition(pid) for pid in ("0", "10", "20")}
        first = write_tagged_rows(tmp_path / "first.parquet", 0, 30, "old")
        await commit_table(
            duckdb,
            table,
            [first],
            PartitionedTablePlan(
                table_signature="sig",
                full_rebuild=True,
                current_partitions=physical,
                changes={
                    pid: PartitionChange(
                        kind="add", partition_id=pid, current=item, path=first
                    )
                    for pid, item in physical.items()
                },
            ),
        )

        paths: list[str] = []
        changes: dict[str, PartitionChange] = {}
        for index, batch in enumerate(batches):
            path = write_tagged_rows(
                tmp_path / f"batch-{index}.parquet",
                int(batch[0]),
                int(batch[-1]) + 10,
                "new",
            )
            paths.append(path)
            for pid in batch:
                changes[pid] = PartitionChange(
                    kind="update",
                    partition_id=pid,
                    current=physical[pid],
                    previous=physical[pid],
                    path=path,
                )

        await commit_table(
            duckdb,
            table,
            paths,
            PartitionedTablePlan(
                table_signature="sig",
                full_rebuild=False,
                current_partitions=physical,
                changes=changes,
            ),
        )

        rows = await duckdb.query("SELECT cpf, tag FROM dl.people ORDER BY cpf")
        assert rows == [
            (cpf, "new" if cpf in expected_new else "old") for cpf in range(30)
        ]


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
