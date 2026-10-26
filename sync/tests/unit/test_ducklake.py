from pathlib import Path
from typing import Final

import pytest
from psycopg.sql import Identifier, Literal

from sync.duckdb import DuckDB
from sync.ducklake import (
    DuckLakePaths,
    commit_table,
    custom_partitioning_expression,
    drop_tables_except,
    evolve_table_schema,
    same_column_type,
    source_partition_column,
    table_prefix,
    table_sort,
    validate_sort_columns,
)
from sync.executor import Executor
from sync.models import (
    DuckLakePartition,
    DuckLakePartitionTransform,
    DuckLakeTableConfig,
    FullTable,
    PartitionChange,
    PartitionedTablePlan,
)
from sync.settings import settings
from sync.sources.partitions import (
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from sync.templates import render_template
from sync.types import DatabaseRow, DuckDBParams
from tests.constants import PARQUET, TEST_SQL_DIR
from tests.helpers import (
    attach_ducklake,
    create_ducklake_table,
    ducklake_columns,
    ducklake_row_count,
    partition,
    snapshot_count,
    table_plan,
)


@pytest.mark.parametrize("schema", ["app", "bigquery_a"])
def test_each_schema_gets_its_own_metadata_schema_in_the_catalog_database(
    schema: str,
) -> None:
    """Point every schema at the catalog database, with the schema name as the metadata schema."""
    paths = DuckLakePaths.for_schema(schema)

    assert paths == DuckLakePaths(
        catalog=f"ducklake:postgres:{settings.DUCKLAKE_CATALOG_URL.get_secret_value()}",
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
        assert len(await ducklake_columns(duckdb, "people")) > 0

    @pytest.mark.asyncio
    async def test_does_nothing_when_every_table_is_listed(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """A second run finds nothing to drop."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")

        assert await drop_tables_except(duckdb, {"people"}) == []


class TestCustomPartitioningExpression:
    """custom_partitioning_expression writes the partition transforms of the configuration as DuckLake SQL."""

    @pytest.mark.parametrize(
        ("partitions", "expected"),
        [
            pytest.param(
                [DuckLakePartition(column="cpf")], '"cpf"', id="identity-is-the-default"
            ),
            pytest.param(
                [
                    DuckLakePartition(
                        column="cpf",
                        transform=DuckLakePartitionTransform.BUCKET,
                        buckets=8,
                    )
                ],
                'bucket(8, "cpf")',
                id="bucket-with-its-count",
            ),
            pytest.param(
                [
                    DuckLakePartition(
                        column="at", transform=DuckLakePartitionTransform.YEAR
                    ),
                    DuckLakePartition(
                        column="at", transform=DuckLakePartitionTransform.MONTH
                    ),
                    DuckLakePartition(
                        column="at", transform=DuckLakePartitionTransform.DAY
                    ),
                    DuckLakePartition(
                        column="at", transform=DuckLakePartitionTransform.HOUR
                    ),
                ],
                'year("at"), month("at"), day("at"), hour("at")',
                id="time-transforms-in-order",
            ),
            pytest.param(
                [DuckLakePartition(column='we"ird')],
                '"we""ird"',
                id="quote-in-a-column-name",
            ),
        ],
    )
    def test_writes_each_transform_over_its_quoted_column(
        self, partitions: list[DuckLakePartition], expected: str
    ) -> None:
        """Quote the column and join the transforms with a comma."""
        assert custom_partitioning_expression(partitions) == expected


class TestSourcePartitionColumn:
    """source_partition_column takes the partition column from the partitions that the source has."""

    @pytest.mark.parametrize(
        "selection",
        [
            pytest.param(
                TimeRangeSelection(column="dt", lower="2026-01-01", upper="2026-01-02"),
                id="time-range",
            ),
            pytest.param(
                RangeSelection(partition_id="10", column="dt", lower=10, upper=20),
                id="range",
            ),
            pytest.param(
                RemainderSelection(column="dt", start=0, end=100), id="remainder"
            ),
        ],
    )
    def test_names_the_column_of_the_first_partition(
        self, selection: TimeRangeSelection | RangeSelection | RemainderSelection
    ) -> None:
        """Return the quoted column, whatever the kind of selection."""
        plan = table_plan(current={"10": partition("10", selection)})

        assert source_partition_column(plan) == '"dt"'

    def test_names_no_column_when_the_source_has_no_partition(self) -> None:
        """Leave the table unpartitioned."""
        assert source_partition_column(table_plan()) is None


class TestConfiguredSort:
    """The sort columns of a table come from its configuration, and they must exist in the source."""

    def test_has_no_sort_when_the_configuration_names_none(self) -> None:
        """Give an empty list, not None."""
        assert table_sort(FullTable(name="p.d.t", resolved_schema="app")) == []

    def test_lists_the_configured_columns_in_order(self) -> None:
        """Keep the order, because it is the sort order."""
        table = FullTable(
            name="p.d.t",
            resolved_schema="app",
            ducklake=DuckLakeTableConfig(sort=["b", "a"]),
        )

        assert table_sort(table) == ["b", "a"]

    def test_refuses_a_sort_column_that_the_source_lacks_and_names_it(self) -> None:
        """List every unknown column, sorted."""
        table = FullTable(
            name="p.d.t",
            resolved_schema="app",
            ducklake=DuckLakeTableConfig(sort=["z", "a", "y"]),
        )

        with pytest.raises(ValueError, match="Unknown DuckLake sort columns: y, z"):
            validate_sort_columns(table, {"a", "b"})

    def test_accepts_sort_columns_that_the_source_has(self) -> None:
        """Raise nothing when every column exists."""
        table = FullTable(
            name="p.d.t",
            resolved_schema="app",
            ducklake=DuckLakeTableConfig(sort=["a"]),
        )

        validate_sort_columns(table, {"a", "b"})
