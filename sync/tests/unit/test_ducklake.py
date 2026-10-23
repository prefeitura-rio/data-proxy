from pathlib import Path
from typing import Final

import pytest
from psycopg.sql import Identifier, Literal

from sync.duckdb import DuckDB
from sync.ducklake import (
    DuckLakePaths,
    commit_table,
    drop_tables_except,
    evolve_table_schema,
    same_column_type,
    table_prefix,
)
from sync.executor import Executor
from sync.models import FullTable, PartitionChange, PartitionedTablePlan
from sync.settings import settings
from sync.templates import render_template
from sync.types import DatabaseRow, DuckDBParams
from tests.constants import PARQUET, TEST_SQL_DIR
from tests.helpers import (
    attach_ducklake,
    create_ducklake_table,
    ducklake_columns,
    ducklake_row_count,
    partition,
)


@pytest.mark.parametrize("schema", ["app", "bigquery_a"])
def test_each_schema_gets_its_own_metadata_schema_in_the_catalog_database(
    schema: str,
) -> None:
    """Point every schema at the catalog database, with the schema name as the metadata schema."""
    paths = DuckLakePaths.for_schema(schema)

    assert paths == DuckLakePaths(
        catalog=f"ducklake:postgres:{settings.DUCKLAKE_CATALOG_URL}",
        data=f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        metadata_schema=schema,
    )


class TestDucklakePartitionCommit:
    """Incremental DuckLake publication behavior."""

    @pytest.mark.asyncio
    async def test_deletes_removed_partition_without_new_paths(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Remove rows for a deleted partition without an insert task."""
        await attach_ducklake(duckdb, tmp_path)
        table = FullTable(name="p.d.people", resolved_schema="app")
        physical = partition("10")
        added = PartitionedTablePlan(
            table_signature="sig",
            full_rebuild=False,
            current_partitions={"10": physical},
            changes={
                "10": PartitionChange(
                    kind="add",
                    partition_id="10",
                    current=physical,
                    path=PARQUET,
                )
            },
        )

        await commit_table(duckdb, table, [PARQUET], added)
        assert await ducklake_row_count(duckdb, "people") == 10

        removed = PartitionedTablePlan(
            table_signature="sig",
            full_rebuild=False,
            current_partitions={},
            changes={
                "10": PartitionChange(
                    kind="remove",
                    partition_id="10",
                    previous=physical,
                )
            },
        )
        await commit_table(duckdb, table, [], removed)

        assert await ducklake_row_count(duckdb, "people") == 0


@pytest.mark.parametrize(
    ("existing", "source", "expected"),
    [
        pytest.param("DOUBLE", "DOUBLE", True, id="same-type"),
        pytest.param("double", "DOUBLE", True, id="case-differs"),
        pytest.param("BIGINT", "DOUBLE", False, id="another-type"),
        pytest.param("DECIMAL(18,3)", "DECIMAL(18,3)", True, id="same-decimal"),
        pytest.param("DECIMAL(18,3)", "DECIMAL(10,2)", False, id="another-decimal"),
        pytest.param(
            "GEOMETRY", "GEOMETRY('OGC:CRS84')", True, id="catalog-keeps-no-crs"
        ),
        pytest.param(
            "GEOMETRY('OGC:CRS84')", "GEOMETRY('OGC:CRS84')", True, id="same-crs"
        ),
        pytest.param("GEOMETRY", "VARCHAR", False, id="geometry-is-not-text"),
        pytest.param(
            "STRUCT(g GEOMETRY)",
            "STRUCT(g GEOMETRY('OGC:CRS84'))",
            True,
            id="geometry-inside-a-struct",
        ),
    ],
)
def test_same_column_type_ignores_the_crs_that_the_catalog_drops(
    existing: str, source: str, expected: bool
) -> None:
    """DuckLake keeps no CRS for a GEOMETRY column, so a table that was just published would look changed."""
    assert same_column_type(existing, source) is expected


class TestDucklakeEvolveSchema:
    """DuckLake schema evolution behavior against a real catalog."""

    @pytest.mark.asyncio
    async def test_adds_drops_and_promotes_columns(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Add new columns, drop removed columns, and promote types."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people", PARQUET)

        evolved = tmp_path / "evolved.parquet"
        await duckdb.execute(
            render_template("duckdb/write_evolved_parquet", {}, root=TEST_SQL_DIR),
            params=[str(evolved)],
        )

        await evolve_table_schema(duckdb, "people", str(evolved))
        rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
            "duckdb/describe_table",
            mapping={"table": Identifier("people")},
            expect=tuple[str, str],
        )
        columns = {row[0]: row[1] for row in rows}
        assert set(columns) == {"cpf", "active", "score"}
        assert columns["cpf"].upper() == "DOUBLE"


async def snapshot_count(duckdb: DuckDB) -> int:
    """Return how many snapshots the attached catalog holds."""
    rows = await duckdb.query("SELECT count(*) FROM ducklake_snapshots('dl')")
    count = rows[0][0]
    assert isinstance(count, int)
    return count


class TestDucklakeMaintenance:
    """DuckLake maintenance against a real catalog and real data files."""

    COMMITS: Final = 4

    @pytest.mark.asyncio
    async def test_expires_snapshots_merges_files_and_keeps_every_row(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """After four commits and a maintenance run with no retention, the old snapshots and files are gone and every row is kept."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)
        for _ in range(self.COMMITS):
            await executor.execute(
                "duckdb/insert_parquet",
                mapping={"table": Identifier("people")},
                params=[[PARQUET]],
            )
        rows = await ducklake_row_count(duckdb, "people")
        snapshots_before = await snapshot_count(duckdb)
        assert len(list((tmp_path / "data").rglob("*.parquet"))) == self.COMMITS

        await executor.execute(
            "duckdb/maintenance",
            mapping={
                "interval": "0 seconds",
                "max_compacted_files": Literal(10),
                "rewrite_delete_threshold": Literal(0.5),
            },
        )

        assert await snapshot_count(duckdb) < snapshots_before
        assert await ducklake_row_count(duckdb, "people") == rows
        assert len(list((tmp_path / "data").rglob("*.parquet"))) == 1


def test_the_files_of_a_table_live_under_its_own_folder_of_the_schema_data_path() -> (
    None
):
    """Address the folder that holds the data files of one table, without the bucket."""
    assert (
        table_prefix("app", "people")
        == f"{settings.DUCKLAKE_CATALOG_PATH}/app/main/people"
    )


class TestDropTablesExcept:
    """Drop the tables that the config no longer lists."""

    @pytest.mark.asyncio
    async def test_drops_the_unlisted_tables_and_returns_their_names(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """A listed table stays, and each other table goes."""
        await attach_ducklake(duckdb, tmp_path)
        for name in ("people", "orders", "items"):
            await create_ducklake_table(duckdb, name)

        dropped = await drop_tables_except(duckdb, {"people"})

        assert dropped == ["items", "orders"]
        assert await ducklake_columns(duckdb, "people")

    @pytest.mark.asyncio
    async def test_does_nothing_when_every_table_is_listed(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """A second run finds nothing to drop."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")

        assert await drop_tables_except(duckdb, {"people"}) == []
