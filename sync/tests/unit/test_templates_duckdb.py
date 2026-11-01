"""Tests for the DuckDB and BigQuery SQL templates."""

from pathlib import Path

import duckdb as duckdb_module
import pytest
from psycopg.sql import Identifier, Literal

from sync.duckdb import DuckDB
from sync.executor import Executor
from sync.sources.bigquery.clients import BigQuery
from sync.sources.bigquery.partitions import PartitionMetadata, partition_rows
from sync.templates import render_template
from sync.types import DatabaseRow, DuckDBParams, TemplateValue
from tests.constants import (
    MODIFIED,
    PARQUET,
)
from tests.helpers import (
    attach_ducklake,
    count_rows,
    create_ducklake_table,
    ducklake_columns,
    ducklake_row_count,
)


class TestDuckdbTemplates:
    """The DuckDB templates, run on a real DuckDB with a DuckLake catalog."""

    @pytest.mark.asyncio
    async def test_create_and_insert_creates_table_from_parquet_schema(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Create one DuckLake table with the Parquet file's columns."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        columns = await ducklake_columns(duckdb, "people")
        assert {"cpf", "name"} <= columns

    @pytest.mark.asyncio
    async def test_create_and_insert_inserts_parquet_rows(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Insert Parquet rows into an existing DuckLake table."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
            "duckdb/insert_parquet",
            mapping={"table": Identifier("people")},
            params=[[PARQUET]],
        )
        assert await ducklake_row_count(duckdb, "people") == 10

    @pytest.mark.asyncio
    async def test_alter_column_adds_and_drops_columns(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
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

    @pytest.mark.asyncio
    async def test_delete_partition_deletes_all_rows_without_predicate(
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

    @pytest.mark.asyncio
    async def test_set_partitioned_by_sets_and_resets_the_partitioning(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """A table has one partition key after a set, and none after a reset."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)
        active = """
            SELECT count(*)
            FROM __ducklake_metadata_dl.ducklake_partition_column AS key
            JOIN __ducklake_metadata_dl.ducklake_partition_info AS info
              ON key.partition_id = info.partition_id
            JOIN __ducklake_metadata_dl.ducklake_table AS table_info
              ON info.table_id = table_info.table_id
            WHERE table_info.table_name = 'people'
              AND table_info.end_snapshot IS NULL
              AND info.end_snapshot IS NULL
        """

        await executor.execute(
            "duckdb/set_partitioned_by",
            mapping={"table": Identifier("people"), "partitioning": '"cpf"'},
        )
        after_set = await duckdb.query(active)
        await executor.execute(
            "duckdb/set_partitioned_by",
            mapping={"table": Identifier("people"), "partitioning": ""},
        )
        after_reset = await duckdb.query(active)

        assert (after_set, after_reset) == ([(1,)], [(0,)])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("table", "expected"),
        [
            pytest.param("people", 1, id="created"),
            pytest.param("missing", 0, id="missing"),
        ],
    )
    async def test_metadata_table_exists_reports_whether_the_table_exists(
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
    async def test_list_tables_returns_the_tables_of_the_catalog(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Return the name of every table that the catalog holds."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        await create_ducklake_table(duckdb, "orders")

        rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
            "duckdb/list_tables", expect=tuple[str]
        )

        assert sorted(rows) == [("orders",), ("people",)]

    @pytest.mark.asyncio
    async def test_drop_table_removes_only_the_named_table(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Drop one table and keep the others."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        await create_ducklake_table(duckdb, "orders")

        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
            "duckdb/drop_table", mapping={"table": Identifier("orders")}
        )

        rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
            "duckdb/list_tables", expect=tuple[str]
        )
        assert rows == [("people",)]

    @pytest.mark.asyncio
    async def test_metadata_get_sort_returns_configured_columns(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Return the sort columns configured with SET SORTED BY, and none after a reset."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)

        await executor.execute(
            "duckdb/set_sorted_by",
            mapping={"table": Identifier("people"), "sort_columns": '"cpf"'},
        )
        configured = await executor.query(
            "duckdb/get_sort",
            {"metadata": "__ducklake_metadata_dl"},
            params=["people"],
            expect=tuple[str],
        )
        await executor.execute(
            "duckdb/set_sorted_by",
            mapping={"table": Identifier("people"), "sort_columns": ""},
        )
        reset = await executor.query(
            "duckdb/get_sort",
            {"metadata": "__ducklake_metadata_dl"},
            params=["people"],
            expect=tuple[str],
        )
        assert (configured, reset) == ([("cpf",)], [])

    @pytest.mark.asyncio
    async def test_metadata_describe_parquet_returns_column_names_and_types(
        self, duckdb: DuckDB
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
    async def test_current_snapshot_moves_forward_with_a_commit(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Return the snapshot of the attached catalog, which a new table advances."""
        await attach_ducklake(duckdb, tmp_path)
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)
        before = await executor.query("duckdb/current_snapshot", expect=tuple[int])

        await create_ducklake_table(duckdb, "people")

        after = await executor.query("duckdb/current_snapshot", expect=tuple[int])
        assert after[0][0] > before[0][0]

    @pytest.mark.asyncio
    async def test_set_option_stores_a_persistent_option(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Set one DuckLake option, and read it back from the catalog."""
        await attach_ducklake(duckdb, tmp_path)

        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
            "duckdb/set_option",
            mapping={"option": "parquet_compression", "value": "'zstd'"},
        )

        rows = await duckdb.query(
            "SELECT value FROM dl.options() WHERE option_name = 'parquet_compression'"
        )
        assert rows == [("zstd",)]

    @pytest.mark.asyncio
    async def test_maintenance_keeps_the_rows(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Run every maintenance call on a table with rows, and lose none of them."""
        await attach_ducklake(duckdb, tmp_path)
        await create_ducklake_table(duckdb, "people")
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)
        await executor.execute(
            "duckdb/insert_parquet",
            mapping={"table": Identifier("people")},
            params=[[PARQUET]],
        )

        await executor.execute(
            "duckdb/maintenance",
            mapping={
                "interval": "7 days",
                "max_compacted_files": Literal(10),
                "rewrite_delete_threshold": Literal(0.5),
            },
        )

        assert await ducklake_row_count(duckdb, "people") == 10

    @pytest.mark.asyncio
    async def test_write_partition_and_remainder_split_the_source_without_overlap(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """The fixture holds cpf 10 to 19: the range 12 to 16 goes to one file, and the other five rows to the other."""
        lower, upper = 12, 17
        mapping: dict[str, TemplateValue] = {
            "json_columns": [],
            "source": f"read_parquet('{PARQUET}')",
            "column": "cpf",
            "lower": Literal(lower),
            "upper": Literal(upper),
        }
        part, rest = tmp_path / "part.parquet", tmp_path / "rest.parquet"
        executor: Executor[DuckDBParams, list[DatabaseRow]] = Executor(conn=duckdb)

        await executor.execute(
            "duckdb/write_partition", mapping={**mapping, "path": f"'{part}'"}
        )
        await executor.execute(
            "duckdb/write_remainder", mapping={**mapping, "path": f"'{rest}'"}
        )

        inside = f"cpf >= {lower} AND cpf < {upper}"
        assert [
            await count_rows(duckdb, part),
            await count_rows(duckdb, part, inside),
            await count_rows(duckdb, rest),
            await count_rows(duckdb, rest, inside),
        ] == [5, 5, 5, 0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("template", "lower", "upper"),
        [
            pytest.param("duckdb/write_all", 0, 25_000, id="all"),
            pytest.param("duckdb/write_partition", 0, 25_000, id="partition"),
            pytest.param("duckdb/write_remainder", 25_000, 30_000, id="remainder"),
        ],
    )
    async def test_every_write_template_makes_small_row_groups(
        self, duckdb: DuckDB, tmp_path: Path, template: str, lower: int, upper: int
    ) -> None:
        """A row group is in memory while DuckDB writes it. A table with wide JSON columns needs more than 2 GB for the default of 122880 rows. DuckDB rounds the size of 10000 up to whole vectors of 2048 rows, so a group has at most 10240 rows."""
        target = tmp_path / "groups.parquet"
        mapping: dict[str, TemplateValue] = {
            "json_columns": [],
            "source": "range(25000) AS t(n)",
            "column": "n",
            "lower": Literal(lower),
            "upper": Literal(upper),
            "path": f"'{target}'",
        }

        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
            template, mapping=mapping
        )
        rows = await duckdb.query(
            f"SELECT count(*), max(row_group_num_rows) FROM parquet_metadata('{target}')"
        )

        assert rows[0][0] == 3
        largest = rows[0][1]
        assert isinstance(largest, int)
        assert largest <= 10_240

    @pytest.mark.asyncio
    async def test_write_all_copies_every_row_of_the_source(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
        """Copy the whole source table to one Parquet file."""
        target = tmp_path / "all.parquet"

        await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
            "duckdb/write_all",
            mapping={
                "json_columns": [],
                "source": f"read_parquet('{PARQUET}')",
                "path": f"'{target}'",
            },
        )

        assert await count_rows(duckdb, target) == await count_rows(
            duckdb, Path(PARQUET)
        )


class TestBigQueryTemplates:
    """The BigQuery template, run with the mocked client."""

    @pytest.mark.asyncio
    async def test_partitions_returns_validated_partition_rows(
        self, bigquery: BigQuery
    ) -> None:
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


@pytest.mark.parametrize(
    ("memory_limit", "threads", "temp_directory"),
    [
        pytest.param("512MB", 1, "spill-a", id="small"),
        pytest.param("2GB", 4, "spill-b", id="large"),
    ],
)
def test_limits_template_sets_the_limits_of_a_duckdb_connection(
    memory_limit: str, threads: int, temp_directory: str
) -> None:
    """Run the limits template on a plain connection and read the settings back."""
    sql = render_template(
        "duckdb/limits",
        {
            "memory_limit": Literal(memory_limit),
            "threads": Literal(threads),
            "temp_directory": Literal(temp_directory),
        },
    )
    reference = duckdb_module.connect(":memory:")
    reference.execute(f"SET memory_limit = '{memory_limit}'")
    expected = reference.execute("SELECT current_setting('memory_limit')").fetchone()
    reference.close()

    connection = duckdb_module.connect(":memory:")
    connection.execute(sql)
    rows = connection.execute(
        """
        SELECT
            current_setting('memory_limit'),
            current_setting('threads'),
            current_setting('temp_directory')
        """
    ).fetchall()
    connection.close()

    assert rows == [
        (
            expected[0] if expected is not None and len(expected) > 0 else None,
            threads,
            temp_directory,
        )
    ]
