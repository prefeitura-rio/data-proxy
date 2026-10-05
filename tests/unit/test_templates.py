"""SQL template rendering and execution tests.

Rendering mechanics are tested with synthetic templates. DuckDB DuckLake
templates are executed against a real in-memory DuckDB instance with a
file-based DuckLake catalog. BigQuery partition templates are tested
against the mocked BigQuery fixture.
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from jinja2 import UndefinedError
from psycopg.sql import Identifier, Literal

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
from data_proxy.sources.bigquery.clients import BigQuery
from data_proxy.sources.bigquery.partitions import PartitionMetadata, partition_rows
from data_proxy.templates import render_template
from data_proxy.types import DatabaseRow, DuckDBParams
from tests.constants import (
    HELM_SQL,
    MODIFIED,
    PARQUET,
    PARQUET_20,
    SOURCE_SQL,
    TEST_SQL_DIR,
)
from tests.helpers import (
    attach_ducklake,
    create_ducklake_table,
    ducklake_columns,
    ducklake_row_count,
    partition,
)


class TestTemplateRendering:
    """SQL template rendering behavior tests."""

    def test_converts_composable_values_to_sql(self, tmp_path: Path) -> None:
        """Convert a composable identifier before rendering SQL."""
        (tmp_path / "query.sql").write_text("SELECT {{ table }};\n")
        rendered = render_template(
            "query", {"table": Identifier("people")}, root=tmp_path
        )
        assert rendered == 'SELECT "people";\n'

    def test_converts_nested_composable_values_to_sql(self, tmp_path: Path) -> None:
        """Convert composables inside lists and mappings before rendering SQL."""
        (tmp_path / "query.sql").write_text(
            "{% for f in functions %}{{ f }} {% endfor %}{{ sources[0].function }}"
        )
        rendered = render_template(
            "query",
            {
                "functions": [Identifier("a_fn"), Identifier("b_fn")],
                "sources": [{"function": Identifier("c_fn")}],
            },
            root=tmp_path,
        )
        assert rendered == '"a_fn" "b_fn" "c_fn"'

    def test_preserves_template_trailing_newline(self, tmp_path: Path) -> None:
        """Preserve the template trailing newline."""
        (tmp_path / "query.sql").write_text("SELECT 1;\n")
        assert render_template("query", {}, root=tmp_path) == "SELECT 1;\n"

    def test_rejects_missing_template_value(self, tmp_path: Path) -> None:
        """Reject a template value that is missing from the mapping."""
        (tmp_path / "query.sql").write_text("SELECT {{ missing }};")
        with pytest.raises(UndefinedError):
            render_template("query", {}, root=tmp_path)

    def test_loads_configured_source_extensions(self) -> None:
        """Load adapter extensions passed by the runtime configuration."""
        rendered = render_template(
            "duckdb/setup",
            {
                "s3_key_id": Literal("key"),
                "s3_secret_key": Literal("secret"),
                "s3_endpoint": Literal("endpoint"),
                "s3_use_ssl": "false",
                "source_extensions": ["bigquery"],
            },
        )

        assert "LOAD bigquery;" in rendered

    def test_renders_an_source_source_in_a_nested_query(self) -> None:
        rendered = render_template(
            "postgres/describe_source",
            {"source": "bigquery_scan('rj-ia-desenvolvimento.dev.test_table')"},
        )

        assert rendered == (
            "\nSELECT *\nFROM duckdb.query(\n    $duck$\n"
            "    DESCRIBE SELECT * FROM bigquery_scan('rj-ia-desenvolvimento.dev.test_table')\n"
            "    $duck$\n)\n"
        )


def template_body(path: Path) -> str:
    """Return a template without its leading metadata comment."""
    return path.read_text().split("#}", 1)[-1]


def template_id(path: Path) -> str:
    """Return a test id that tells apart templates with the same file name."""
    return path.relative_to(Path(__file__).parents[2]).as_posix()


def template_description(path: Path) -> str:
    """Return the description in the metadata comment of a template."""
    match = re.search(r'"description":\s*"([^"]*)"', path.read_text())
    return match.group(1) if match else ""


ALL_TEMPLATES = sorted([*SOURCE_SQL.rglob("*.sql"), *HELM_SQL.rglob("*.sql")])
SECURITY_DEFINER_TEMPLATES = [
    path for path in ALL_TEMPLATES if "SECURITY DEFINER" in template_body(path)
]


class TestSecurityDefinerTemplates:
    """A SECURITY DEFINER function runs with its owner rights, so it must not trust the caller search_path."""

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param(path, id=template_id(path))
            for path in SECURITY_DEFINER_TEMPLATES
        ],
    )
    def test_pins_the_search_path(self, path: Path) -> None:
        """Pin the search_path of every SECURITY DEFINER function."""
        assert re.search(
            r"SET search_path = pg_catalog, [^;]*pg_temp", template_body(path)
        )

    def test_describes_only_what_the_function_declares(self) -> None:
        """Claim SECURITY DEFINER in a description only when the function declares it."""
        claimed = [
            template_id(path)
            for path in ALL_TEMPLATES
            if "SECURITY DEFINER" in template_description(path)
            and "SECURITY DEFINER" not in template_body(path)
        ]

        assert claimed == []


class TestTemplateOwnership:
    """A procedure has one SQL source, so two installers cannot install different bodies."""

    def test_no_template_exists_in_both_folders_except_the_shared_files(self) -> None:
        """Keep the sync service and the Helm jobs from owning the same procedure."""

        def names(root: Path) -> set[str]:
            return {path.relative_to(root).as_posix() for path in root.rglob("*.sql")}

        assert names(SOURCE_SQL) & names(HELM_SQL) == {
            "macros.sql",
            "cleanup_stale_objects.sql",
            "prune_access_log.sql",
        }


class TestDuckdbCreateAndInsert:
    """DuckLake create_table and insert_parquet template behavior."""

    @pytest.mark.asyncio
    async def test_creates_table_from_parquet_schema(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Create one DuckLake table with the Parquet file's columns."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        columns = await ducklake_columns(duckdb, "people")
        assert {"cpf", "name"} <= columns

    @pytest.mark.asyncio
    async def test_inserts_parquet_rows(self, duckdb: DuckDB, tmp_path: Path) -> None:
        """Insert Parquet rows into an existing DuckLake table."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
            "duckdb/insert_parquet",
            mapping={"table": Identifier("people")},
            params=[[PARQUET]],
        )
        assert await ducklake_row_count(duckdb, "people") == 10


class TestDuckdbAlterColumn:
    """DuckLake alter_column template behavior."""

    @pytest.mark.asyncio
    async def test_adds_and_drops_columns(self, duckdb: DuckDB, tmp_path: Path) -> None:
        """Add a column, then drop it, and verify the schema each time."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
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
        assert "active" in await ducklake_columns(duckdb, "people")

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
        assert "active" not in await ducklake_columns(duckdb, "people")


class TestDuckdbDeletePartition:
    """DuckLake delete_partition template behavior."""

    @pytest.mark.asyncio
    async def test_deletes_all_rows_without_predicate(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Delete every row when no predicate is given."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
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
        count = await ducklake_row_count(duckdb, "people")
        assert count == 0


class TestDuckdbSortAndPartitioning:
    """DuckLake set_sorted_by and set_partitioned_by template behavior."""

    @pytest.mark.asyncio
    async def test_sets_and_resets_sort(self, duckdb: DuckDB, tmp_path: Path) -> None:
        """Set sort columns and then reset them without error."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
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
        await create_ducklake_table(duckdb, "people")
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
    @pytest.mark.parametrize(
        ("table", "expected"),
        [
            pytest.param("people", 1, id="created"),
            pytest.param("missing", 0, id="missing"),
        ],
    )
    async def test_table_exists_reports_whether_the_table_exists(
        self, duckdb: DuckDB, tmp_path: Path, table: str, expected: int
    ) -> None:
        """Report 1 for a created DuckLake table and 0 for a missing one."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
            "duckdb/table_exists",
            params=[table],
            expect=tuple[int],
        )
        assert rows[0][0] == expected

    @pytest.mark.asyncio
    async def test_get_sort_returns_configured_columns(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Return the sort columns configured with SET SORTED BY."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
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
        assert rows == [("cpf",)]

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


class TestBigQueryPartitionTemplate:
    """BigQuery partition metadata template behavior using the mocked fixture."""

    @pytest.mark.asyncio
    async def test_returns_validated_partition_rows(self, bigquery: BigQuery) -> None:
        """Return validated partition metadata rows from the mocked BigQuery."""
        rows = await partition_rows(bigquery, "test", "dataset", "range_buckets")

        assert {row.partition_id: row for row in rows} == {
            partition_id: PartitionMetadata(
                partition_id=partition_id,
                last_modified_time=MODIFIED.replace(tzinfo=None),
                logical_bytes=1_000_000,
            )
            for partition_id in ("0", "20")
        }


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
        assert await ducklake_row_count(duckdb, "people") == 10

        await commit_table(duckdb, table, [PARQUET_20], None)
        assert await ducklake_row_count(duckdb, "people") == 10


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

        assert result.published_tables == {"p.d.people"}
        assert await ducklake_row_count(duckdb, "people") == 10

    @pytest.mark.asyncio
    async def test_returns_committed_snapshot_id(
        self, duckdb: DuckDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the snapshot that holds the published tables."""
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

        result = await publish_schema(duckdb, AsyncMock(), config, plan, set())

        rows = await duckdb.query("SELECT id FROM dl.current_snapshot()")
        assert result.snapshot_id == rows[0][0]

    @pytest.mark.asyncio
    async def test_returns_no_snapshot_when_nothing_is_published(
        self, duckdb: DuckDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return no snapshot when every table is blocked by a failed path."""
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

        result = await publish_schema(duckdb, AsyncMock(), config, plan, {PARQUET})

        assert result.published_tables == set()
        assert result.snapshot_id is None
