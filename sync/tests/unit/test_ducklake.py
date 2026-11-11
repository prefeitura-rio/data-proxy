import asyncio
from pathlib import Path
from typing import Final
from unittest.mock import AsyncMock

import duckdb as duckdb_module
import pytest
from psycopg.sql import Identifier, Literal

from sync import ducklake as ducklake_module
from sync.duckdb import DuckDB
from sync.ducklake import (
    DuckLakePaths,
    affected_predicates,
    apply_partitioning,
    commit_table,
    custom_partitioning_expression,
    drop_tables_except,
    evolve_table_schema,
    planned_paths,
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
    SyncPlan,
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
from tests.helpers.core import exactly
from tests.helpers.ducklake import (
    attach_ducklake,
    create_ducklake_table,
    data_files,
    ducklake_columns,
    ducklake_row_count,
    snapshot_count,
    write_unsorted_batches,
)
from tests.helpers.state import partition, plan_of_kinds, table_plan


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


class TestSortedCommitInsertsOneBatchAtATime:
    """A sorted table inserts each batch file by itself, so a sort holds one batch and not the whole table."""

    @pytest.mark.asyncio
    async def test_a_sorted_table_gets_one_file_for_each_batch_with_sorted_row_groups(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        await attach_ducklake(duckdb, tmp_path)
        paths = write_unsorted_batches(tmp_path, 3)
        table = FullTable(
            name="p.d.people",
            resolved_schema="app",
            ducklake=DuckLakeTableConfig(sort=["cpf"]),
        )

        await commit_table(duckdb, table, paths, None)

        assert await ducklake_row_count(duckdb, "people") == 90000
        assert await data_files(duckdb) == 3
        overlaps = await duckdb.query(
            f"""
            WITH groups AS (
                SELECT file_name, row_group_id,
                       stats_min_value::BIGINT AS lo, stats_max_value::BIGINT AS hi
                FROM parquet_metadata('{tmp_path}/data/**/*.parquet')
                WHERE path_in_schema = 'cpf'
            )
            SELECT count(*) FROM groups AS a JOIN groups AS b
              ON a.file_name = b.file_name AND a.row_group_id < b.row_group_id
             AND a.lo <= b.hi AND b.lo <= a.hi
            """
        )
        assert overlaps == [(0,)]

    @pytest.mark.asyncio
    async def test_a_table_without_sort_columns_keeps_one_insert(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        await attach_ducklake(duckdb, tmp_path)
        paths = write_unsorted_batches(tmp_path, 3)
        table = FullTable(name="p.d.people", resolved_schema="app")

        await commit_table(duckdb, table, paths, None)

        assert await ducklake_row_count(duckdb, "people") == 90000
        assert await data_files(duckdb) == 1

    @pytest.mark.asyncio
    async def test_a_failed_second_insert_rolls_the_whole_commit_back(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        await attach_ducklake(duckdb, tmp_path)
        first, second = write_unsorted_batches(tmp_path, 2)
        table = FullTable(
            name="p.d.people",
            resolved_schema="app",
            ducklake=DuckLakeTableConfig(sort=["cpf"]),
        )

        with pytest.raises(duckdb_module.IOException):
            await commit_table(
                duckdb, table, [first, second, str(tmp_path / "missing.parquet")], None
            )

        assert await ducklake_row_count(duckdb, "people") == 0
        assert await data_files(duckdb) == 0


class TestRangeSourceKeepsTheTableUnpartitioned:
    """A table from an integer range source gets no partition key, so the commit makes few files."""

    @pytest.mark.asyncio
    async def test_commit_of_a_range_source_sets_no_partition_key(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """The column cpf has a different value in each row, and a key on it would make one partition for each row."""
        await attach_ducklake(duckdb, tmp_path)
        table = FullTable(name="p.d.people", resolved_schema="app")
        physical = partition("10")
        plan = PartitionedTablePlan(
            table_signature="sig",
            full_rebuild=True,
            current_partitions={"10": physical},
            changes={
                "10": PartitionChange(
                    kind="add", partition_id="10", current=physical, path=PARQUET
                )
            },
        )

        await commit_table(duckdb, table, [PARQUET], plan)

        keys = await duckdb.query(
            """
            SELECT count(*)
            FROM __ducklake_metadata_dl.ducklake_partition_column AS key
            JOIN __ducklake_metadata_dl.ducklake_partition_info AS info
              ON key.partition_id = info.partition_id
            WHERE info.end_snapshot IS NULL
            """
        )
        files = await duckdb.query(
            "SELECT count(*) FROM ducklake_list_files('dl', 'people')"
        )
        assert (keys, files) == ([(0,)], [(1,)])


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
    """source_partition_column names a DuckLake partition column only for a time source."""

    def test_names_the_column_of_a_time_partition(self) -> None:
        """Return the quoted column, so a day of the source is a partition of the table."""
        selection = TimeRangeSelection(
            column="dt", lower="2026-01-01", upper="2026-01-02"
        )
        plan = table_plan(current={"20260101": partition("20260101", selection)})

        assert source_partition_column(plan) == '"dt"'

    @pytest.mark.parametrize(
        "selection",
        [
            pytest.param(
                RangeSelection(partition_id="10", column="dt", lower=10, upper=20),
                id="range",
            ),
            pytest.param(
                RemainderSelection(column="dt", start=0, end=100), id="remainder"
            ),
        ],
    )
    def test_names_no_column_for_an_integer_range_source(
        self, selection: RangeSelection | RemainderSelection
    ) -> None:
        """The value of an integer range column can be unique in each row, and a partition for each value breaks the commit."""
        plan = table_plan(current={"10": partition("10", selection)})

        assert source_partition_column(plan) is None

    def test_names_no_column_when_the_source_has_no_partition(self) -> None:
        """Leave the table unpartitioned."""
        assert source_partition_column(table_plan()) is None


class TestApplyPartitioning:
    """The partitioning of a table follows the configuration and the source, and a stale one is reset."""

    @staticmethod
    def run(
        monkeypatch: pytest.MonkeyPatch,
        *,
        configured: list[DuckLakePartition] | None,
        plan: PartitionedTablePlan | None,
        existing: bool,
        stored: bool,
    ) -> list[str]:
        """Apply the partitioning and return the partitioning of each statement that ran."""
        statements: list[str] = []

        async def has_partitioning(conn: object, table: object) -> bool:
            return stored

        async def execute(
            self: Executor[DuckDBParams, list[DatabaseRow]],
            path: str,
            mapping: dict[str, object] | None = None,
            **_: object,
        ) -> None:
            statements.append(
                str({} if mapping is None else mapping.get("partitioning"))
            )

        monkeypatch.setattr(ducklake_module, "has_partitioning", has_partitioning)
        monkeypatch.setattr(Executor, "execute", execute)
        table = FullTable(
            name="p.d.t",
            resolved_schema="app",
            ducklake=DuckLakeTableConfig(partitioning=configured),
        )

        asyncio.run(
            apply_partitioning(
                AsyncMock(spec=DuckDB), Identifier("t"), table, plan, existing
            )
        )

        return statements

    @pytest.mark.parametrize(
        ("kind", "existing", "stored", "expected"),
        [
            pytest.param(
                "range", False, False, [], id="new-range-table-stays-unpartitioned"
            ),
            pytest.param(
                "range", True, False, [], id="range-table-without-a-key-is-left-alone"
            ),
            pytest.param(
                "range", True, True, [""], id="range-table-with-a-stale-key-is-reset"
            ),
            pytest.param(
                "time",
                False,
                False,
                ['"dt"'],
                id="time-table-is-partitioned-by-the-source-column",
            ),
            pytest.param(
                "full", True, False, [], id="full-table-without-a-key-is-left-alone"
            ),
            pytest.param(
                "full", True, True, [""], id="full-table-with-a-stale-key-is-reset"
            ),
        ],
    )
    def test_follows_the_source_and_resets_a_stale_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        existing: bool,
        stored: bool,
        expected: list[str],
    ) -> None:
        selections: dict[str, RangeSelection | TimeRangeSelection | None] = {
            "range": RangeSelection(partition_id="10", column="dt", lower=10, upper=20),
            "time": TimeRangeSelection(
                column="dt", lower="2026-01-01", upper="2026-01-02"
            ),
            "full": None,
        }
        selection = selections[kind]
        plan = (
            None
            if selection is None
            else table_plan(current={"10": partition("10", selection)})
        )

        assert (
            self.run(
                monkeypatch,
                configured=None,
                plan=plan,
                existing=existing,
                stored=stored,
            )
            == expected
        )

    def test_a_configured_partitioning_wins_over_the_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan = table_plan(
            current={
                "10": partition(
                    "10",
                    RangeSelection(partition_id="10", column="dt", lower=10, upper=20),
                )
            }
        )
        configured = [
            DuckLakePartition(
                column="id", transform=DuckLakePartitionTransform.BUCKET, buckets=8
            )
        ]

        assert self.run(
            monkeypatch, configured=configured, plan=plan, existing=True, stored=True
        ) == ['bucket(8, "id")']


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

        with pytest.raises(
            ValueError, match=exactly("Unknown DuckLake sort columns: y, z")
        ):
            validate_sort_columns(table, {"a", "b"})

    def test_accepts_sort_columns_that_the_source_has(self) -> None:
        """Raise nothing when every column exists."""
        table = FullTable(
            name="p.d.t",
            resolved_schema="app",
            ducklake=DuckLakeTableConfig(sort=["a"]),
        )

        validate_sort_columns(table, {"a", "b"})


class TestPlannedPaths:
    """Many partitions share the file of their batch, and the insert reads each file once."""

    def test_lists_each_batch_file_once_in_order(self) -> None:
        changes = {
            pid: PartitionChange(
                kind="add", partition_id=pid, current=partition(pid), path=path
            )
            for pid, path in (("0", "a"), ("10", "a"), ("20", "b"), ("30", "b"))
        }
        plan = table_plan(
            current={pid: partition(pid) for pid in changes}, changes=changes
        )

        assert planned_paths(SyncPlan(schema_name="app"), "t", plan) == ["a", "b"]

    def test_leaves_out_a_removed_partition(self) -> None:
        changes = {
            "0": PartitionChange(
                kind="add", partition_id="0", current=partition("0"), path="a"
            ),
            "10": PartitionChange(
                kind="remove", partition_id="10", previous=partition("10")
            ),
        }

        plan = table_plan(current={"0": partition("0")}, changes=changes)

        assert planned_paths(SyncPlan(schema_name="app"), "t", plan) == ["a"]


class TestAffectedPredicates:
    """The delete of an incremental commit has one predicate for each run of contiguous partitions."""

    def test_joins_contiguous_changed_partitions_into_one_predicate(self) -> None:
        plan = plan_of_kinds(
            stored={"20": "remove"},
            current={"0": "add", "10": "update", "30": "add"},
        )

        assert affected_predicates(plan) == [
            '("cpf" >= 0 AND "cpf" < 40)',
        ]

    def test_an_unchanged_partition_splits_the_predicate(self) -> None:
        plan = plan_of_kinds(
            stored={}, current={"0": "add", "10": "same", "20": "update"}
        )

        assert affected_predicates(plan) == [
            '("cpf" >= 0 AND "cpf" < 10)',
            '("cpf" >= 20 AND "cpf" < 30)',
        ]

    def test_has_no_predicate_when_nothing_changed(self) -> None:
        assert (
            affected_predicates(plan_of_kinds(stored={}, current={"0": "same"})) == []
        )
