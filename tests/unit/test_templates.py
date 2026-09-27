"""SQL template rendering and execution tests.

Rendering mechanics are tested with synthetic templates. DuckDB DuckLake
templates are executed against a real in-memory DuckDB instance with a
file-based DuckLake catalog. BigQuery partition templates are tested
against the mocked BigQuery fixture.
"""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from jinja2 import UndefinedError
from psycopg.sql import Identifier, Literal

from data_proxy.bigquery.clients import BigQuery
from data_proxy.bigquery.partitions import partition_rows
from data_proxy.duckdb import DuckDB
from data_proxy.ducklake import (
    DuckLakePaths,
    commit_table,
    evolve_table_schema,
    publish_schema,
)
from data_proxy.executor import Executor
from data_proxy.models import (
    FullTable,
    PartitionChange,
    PartitionedTablePlan,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
)
from data_proxy.templates import render_template
from data_proxy.types import DatabaseRow, DuckDBParams
from tests.helpers import partition

PARQUET = str(Path(__file__).parent.parent / "files" / "people_partition_10.parquet")


# ---------------------------------------------------------------------------
# Rendering mechanics
# ---------------------------------------------------------------------------


class TestTemplateRendering:
    """SQL template rendering behavior tests."""

    def test_converts_composable_values_to_sql(self, tmp_path: Path) -> None:
        """Convert a composable identifier before rendering SQL."""
        (tmp_path / "query.sql").write_text("SELECT {{ table }};\n")
        rendered = render_template(
            "query", {"table": Identifier("people")}, root=tmp_path
        )
        assert rendered == 'SELECT "people";\n'

    def test_preserves_template_trailing_newline(self, tmp_path: Path) -> None:
        """Preserve the template trailing newline."""
        (tmp_path / "query.sql").write_text("SELECT 1;\n")
        assert render_template("query", {}, root=tmp_path) == "SELECT 1;\n"

    def test_rejects_missing_template_value(self, tmp_path: Path) -> None:
        """Reject a template value that is missing from the mapping."""
        (tmp_path / "query.sql").write_text("SELECT {{ missing }};")
        with pytest.raises(UndefinedError):
            render_template("query", {}, root=tmp_path)


# ---------------------------------------------------------------------------
# DuckDB DuckLake template execution
# ---------------------------------------------------------------------------


async def attach_ducklake(duckdb: DuckDB, tmp_path: Path) -> None:
    """Attach one file-based DuckLake catalog for the test connection."""
    catalog = tmp_path / "catalog.sqlite"
    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
        "duckdb/attach",
        mapping={
            "catalog": Literal(f"ducklake:sqlite:{catalog}"),
            "data_path": Literal(str(tmp_path / "data")),
            "encrypted": False,
        },
    )


async def create_table(duckdb: DuckDB, name: str, parquet: str = PARQUET) -> None:
    """Create one DuckLake table from a Parquet schema."""
    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
        "duckdb/create_table",
        mapping={"table": Identifier(name)},
        params=[parquet],
    )


async def row_count(duckdb: DuckDB, table: str) -> int:
    """Return the row count of one DuckLake table."""
    rows = await duckdb.query(f'SELECT count(*) FROM dl."{table}"')
    return cast("int", rows[0][0])


async def describe(duckdb: DuckDB, name: str) -> set[str]:
    """Return the column names of one DuckLake table."""
    rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
        "duckdb/describe_table",
        mapping={"table": Identifier(name)},
        expect=tuple[str, str],
    )
    return {row[0] for row in rows}


class TestDuckdbCreateAndInsert:
    """DuckLake create_table and insert_parquet template behavior."""

    @pytest.mark.asyncio
    async def test_creates_table_from_parquet_schema(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Create one DuckLake table with the Parquet file's columns."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people")
        columns = await describe(duckdb, "people")
        assert {"cpf", "name"} <= columns

    @pytest.mark.asyncio
    async def test_inserts_parquet_rows(self, duckdb: DuckDB, tmp_path: Path) -> None:
        """Insert Parquet rows into an existing DuckLake table."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people")
        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
            "duckdb/insert_parquet",
            mapping={"table": Identifier("people")},
            params=[[PARQUET]],
        )
        assert await row_count(duckdb, "people") > 0


class TestDuckdbAlterColumn:
    """DuckLake alter_column template behavior."""

    @pytest.mark.asyncio
    async def test_adds_and_drops_columns(self, duckdb: DuckDB, tmp_path: Path) -> None:
        """Add a column, then drop it, and verify the schema each time."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)

        await executor.execute(
            "duckdb/alter_column",
            mapping={
                "table": Identifier("people"),
                "alterations": [
                    {
                        "operation": "add",
                        "column": Identifier("active").as_string(None),
                        "type": "BOOLEAN",
                    }
                ],
            },
        )
        assert "active" in await describe(duckdb, "people")

        await executor.execute(
            "duckdb/alter_column",
            mapping={
                "table": Identifier("people"),
                "alterations": [
                    {
                        "operation": "drop",
                        "column": Identifier("active").as_string(None),
                        "type": "",
                    }
                ],
            },
        )
        assert "active" not in await describe(duckdb, "people")


class TestDuckdbDeletePartition:
    """DuckLake delete_partition template behavior."""

    @pytest.mark.asyncio
    async def test_deletes_all_rows_without_predicate(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Delete every row when no predicate is given."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)

        await executor.execute(
            "duckdb/insert_parquet",
            mapping={"table": Identifier("people")},
            params=[[PARQUET]],
        )
        await executor.execute(
            "duckdb/delete_partition",
            mapping={"table": Identifier("people"), "predicate": ""},
        )
        count = await row_count(duckdb, "people")
        assert count == 0


class TestDuckdbSortAndPartitioning:
    """DuckLake set_sorted_by and set_partitioned_by template behavior."""

    @pytest.mark.asyncio
    async def test_sets_and_resets_sort(self, duckdb: DuckDB, tmp_path: Path) -> None:
        """Set sort columns and then reset them without error."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)

        await executor.execute(
            "duckdb/set_sorted_by",
            mapping={"table": Identifier("people"), "sort_columns": '"cpf"'},
        )
        await executor.execute(
            "duckdb/set_sorted_by",
            mapping={"table": Identifier("people"), "sort_columns": ""},
        )

    @pytest.mark.asyncio
    async def test_sets_and_resets_partitioning(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Set partition keys and then reset them without error."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)

        await executor.execute(
            "duckdb/set_partitioned_by",
            mapping={"table": Identifier("people"), "partitioning": '"cpf"'},
        )
        await executor.execute(
            "duckdb/set_partitioned_by",
            mapping={"table": Identifier("people"), "partitioning": ""},
        )


class TestDuckdbMetadataTemplates:
    """DuckLake metadata query template behavior."""

    @pytest.mark.asyncio
    async def test_table_exists_returns_true_for_created_table(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Report that a created DuckLake table exists."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people")
        rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
            "duckdb/table_exists",
            params=["people"],
            expect=tuple[int],
        )
        assert rows[0][0] == 1

    @pytest.mark.asyncio
    async def test_table_exists_returns_zero_for_missing_table(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Report that a non-existent DuckLake table does not exist."""
        await attach_ducklake(duckdb, tmp_path)
        rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
            "duckdb/table_exists",
            params=["nonexistent"],
            expect=tuple[int],
        )
        assert rows[0][0] == 0

    @pytest.mark.asyncio
    async def test_get_sort_returns_configured_columns(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Return the sort columns configured with SET SORTED BY."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)

        await executor.execute(
            "duckdb/set_sorted_by",
            mapping={"table": Identifier("people"), "sort_columns": '"cpf"'},
        )
        rows = await executor.query(
            "duckdb/get_sort",
            params=["people"],
            expect=tuple[str],
        )
        assert any("cpf" in row[0] for row in rows)

    @pytest.mark.asyncio
    async def test_describe_parquet_returns_column_names_and_types(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Return column names and types from a Parquet file."""
        rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
            "duckdb/describe_parquet",
            params=[PARQUET],
            expect=tuple[str, str],
        )
        columns = {row[0] for row in rows}
        assert {"cpf", "name"} <= columns

    @pytest.mark.asyncio
    async def test_set_option_executes_without_error(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Set one DuckLake catalog option without error."""
        await attach_ducklake(duckdb, tmp_path)
        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
            "duckdb/set_option",
            mapping={
                "option": "target_file_size",
                "value": Literal("128MB"),
            },
        )


# ---------------------------------------------------------------------------
# BigQuery partition template execution (mocked)
# ---------------------------------------------------------------------------


class TestBigQueryPartitionTemplate:
    """BigQuery partition metadata template behavior using the mocked fixture."""

    @pytest.mark.asyncio
    async def test_returns_validated_partition_rows(self, bigquery: BigQuery) -> None:
        """Return validated partition metadata rows from the mocked BigQuery."""
        rows = await partition_rows(bigquery, "test", "dataset", "range_buckets")

        assert len(rows) >= 1
        for row in rows:
            assert row.partition_id
            assert row.last_modified_time is not None


# ---------------------------------------------------------------------------
# DuckLake publication pipeline (real DuckDB)
# ---------------------------------------------------------------------------


PARQUET_20 = str(Path(__file__).parent.parent / "files" / "people_partition_20.parquet")


class TestDucklakeCommitTable:
    """DuckLake commit_table behavior against a real catalog."""

    @pytest.mark.asyncio
    async def test_inserts_and_replaces_rows(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Insert rows from Parquet, then replace them with a new file."""
        await attach_ducklake(duckdb, tmp_path)
        table = FullTable(name="p.d.people", resolved_schema="app")

        await commit_table(duckdb, table, [PARQUET], None)
        count = await row_count(duckdb, "people")
        assert count > 0

        first_count = count
        await commit_table(duckdb, table, [PARQUET_20], None)
        count = await row_count(duckdb, "people")
        assert count == first_count


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
        assert await row_count(duckdb, "people") > 0

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

        assert await row_count(duckdb, "people") == 0


class TestDucklakeEvolveSchema:
    """DuckLake schema evolution behavior against a real catalog."""

    @pytest.mark.asyncio
    async def test_adds_drops_and_promotes_columns(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Add new columns, drop removed columns, and promote types."""
        await attach_ducklake(duckdb, tmp_path)
        await create_table(duckdb, "people", PARQUET)

        evolved = tmp_path / "evolved.parquet"
        copy_sql = (
            "COPY (SELECT 1::DOUBLE as cpf, true::BOOLEAN as active, "
            "42::INTEGER as score) TO ? (FORMAT PARQUET)"
        )
        await duckdb.execute(copy_sql, params=[str(evolved)])

        async with duckdb.transaction():
            await evolve_table_schema(duckdb, "people", str(evolved))
        rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
            "duckdb/describe_table",
            mapping={"table": Identifier("people")},
            expect=tuple[str, str],
        )
        columns = {row[0]: row[1] for row in rows}
        assert set(columns) == {"cpf", "active", "score"}
        assert columns["cpf"].upper() == "DOUBLE"


class TestDucklakePublishSchema:
    """DuckLake publish_schema behavior against a real catalog."""

    @pytest.mark.asyncio
    async def test_publishes_full_table_into_catalog(
        self, duckdb: DuckDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Create a DuckLake table and insert rows from a full-table plan."""
        catalog = tmp_path / "catalog.sqlite"
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        def local_paths(schema: str) -> DuckLakePaths:
            return DuckLakePaths(catalog=catalog, data=str(data_dir))

        monkeypatch.setattr(DuckLakePaths, "for_schema", staticmethod(local_paths))

        table = FullTable(name="p.d.people", resolved_schema="app")
        config = SyncConfig(schemas={"app": SchemaConfig(tables=[table])})
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.d.people": "sig"},
            paths={"p.d.people": [PARQUET]},
        )
        pg_conn = AsyncMock()

        result = await publish_schema(duckdb, pg_conn, config, plan, set())

        assert "p.d.people" in result.published_tables
        count = await row_count(duckdb, "people")
        assert count > 0
