import itertools
import json
import operator
import re
from asyncio import timeout
from collections.abc import Callable, Iterator
from os import environ
from pathlib import Path
from random import Random
from time import perf_counter, time
from typing import Final, LiteralString, cast

import psycopg
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg.sql import SQL, Identifier, Literal

from sync.conditions import selection_condition
from sync.duckdb import DuckDB
from sync.executor import Executor
from sync.models import (
    FullTable,
    PartitionedTable,
    PublicationResult,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncPlan,
    TableConfig,
    TableState,
)
from sync.postgres import Postgres as PgBackend
from sync.schema import notify_postgrest, wait_for_standbys
from sync.settings import settings
from sync.sources.bigquery.clients import BigQuery
from sync.sources.bigquery.partitions import PartitionMetadata, partition_rows
from sync.sources.partitions import (
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from sync.state import (
    build_table_states,
    delete_removed_state,
    emit_error,
    write_table_states,
)
from sync.templates import render_template
from sync.types import DatabaseRow, DuckDBParams, PostgresParams, TemplateValue
from sync.views import stages
from sync.views.reconcile import reconcile_views
from tests.constants import (
    HELM_SQL,
    MODIFIED,
    PARQUET,
    POSTGRES_URL,
    PROXY_FILTER_CASES,
    ROUTED_TABLE,
    SOURCE_SQL,
    TEST_SQL_DIR,
)
from tests.fixtures.types import Postgres, Psql
from tests.helpers import (
    RBAC,
    SOURCE_HASH,
    RbacRole,
    RbacRules,
    act_as_token,
    attach_ducklake,
    call_table_function,
    create_access_policy,
    create_ducklake_table,
    create_state_tables,
    ducklake_columns,
    ducklake_row_count,
    execute_sql,
    fetch_all,
    function_exists,
    grant_unit,
    helm_sql,
    initialize_schemas,
    insert_access_policy,
    install_console_triggers,
    install_rbac,
    install_table_function,
    make_serving_login,
    partitioned,
    plan_of,
    psql_script,
    publish_people_catalog,
    put_state,
    queued_workflows,
    relation_exists,
    response_headers,
    revision,
    scalar,
    schema_scope_condition,
    set_request_headers,
    set_setting,
    stored_roles,
    verify_rbac,
)

SKIPPED_TEMPLATES = {
    "macros": "Macros are rendered by the templates that import them.",
    "call_cleanup_stale_objects": "A one-line call of a procedure that has its own test.",
    "call_prune_access_log": "A one-line call of a procedure that has its own test.",
    "call_recover_orphaned_workflows": "A one-line call of a procedure that has its own test.",
    "call_reload_postgrest": "A one-line call of a function that has its own test.",
    "grant_select": "A grant to a role. The roles belong to the end-to-end suite.",
    "grant_app_usage": "Grants to roles belong to the end-to-end suite.",
    "grant_console_usage": "Grants to roles belong to the end-to-end suite.",
    "grant_proxy_config": "Grants to roles belong to the end-to-end suite.",
    "set_db_schemas": "A setting of a role belongs to the end-to-end suite.",
    "reset_db_schemas": "A setting of a role belongs to the end-to-end suite.",
    "configure_catalog": "Privileges of roles on the catalog belong to the end-to-end suite.",
    "configure_ducklake": "Persistent secrets of DuckDB that every DuckDB test creates.",
    "attach": "Every DuckDB test runs it through attach_ducklake.",
    "setup": "Every DuckDB test runs it when the connection opens.",
    "describe_source": "It describes a BigQuery table, which needs the real service.",
    "load_source": "It loads a BigQuery extension, which needs the real service.",
    "notify_postgrest": "Run by the reload test through the sync code.",
    "wait_for_standbys": "Run by the reload test through the sync code.",
    "read_sync_config": "A one-line read of the console function.",
    "create_console_functions": "Needs the console schema and the DBOS tables. The end-to-end suite tests it.",
    "create_console_operations": "Needs the console schema and the DBOS tables. The end-to-end suite tests it.",
    "create_console_config": "Needs the console schema. The end-to-end suite tests it.",
    "create_current_snapshot_function": "Installed by reconcile_views, and run by the current snapshot test.",
    "create_table_changes_function": "Installed by reconcile_views, and run by the changes function test.",
    "create_table_files_function": "Installed by reconcile_views, and run by the table files test.",
    "drop_function": "Run by reconcile_views in the reconcile tests.",
    "drop_view_objects": "Run by reconcile_views in the reconcile tests.",
    "list_functions": "Run by reconcile_views in the reconcile tests.",
    "list_view_signatures": "Run by reconcile_views in the reconcile tests.",
    "list_views": "Run by reconcile_views in the reconcile tests.",
    "write_view_signature": "Run by reconcile_views in the reconcile tests.",
    "insert_error": "Run by the state tests of the sync code.",
    "insert_scope": "Run by the state tests of the sync code.",
    "read_state": "Run by the state tests of the sync code.",
    "upsert_state": "Run by the state tests of the sync code.",
    "delete_removed_state": "Run by the state tests of the sync code.",
}


async def count_rows(duckdb: DuckDB, path: Path, where: str = "TRUE") -> int:
    """Count the rows of one Parquet file that match a condition."""
    rows = await duckdb.query(
        f"SELECT count(*) FROM read_parquet('{path}') WHERE {where}"
    )
    count = rows[0][0]
    assert isinstance(count, int)
    return count


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
    async def test_sort_and_partitioning_sets_and_resets_sort(
        self, duckdb: DuckDB, tmp_path: Path
    ) -> None:
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
    async def test_sort_and_partitioning_sets_and_resets_partitioning(
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
            {"metadata": "__ducklake_metadata_dl"},
            params=["people"],
            expect=tuple[str],
        )
        assert rows == [("cpf",)]

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


def test_every_template_has_a_test_or_a_reason_to_skip() -> None:
    """A template that no template test names, and that isn't on the list of skipped ones, fails here."""
    here = Path(__file__).parents[1]
    source = "\n".join(
        path.read_text()
        for path in [
            Path(__file__),
            here / "helpers.py",
            *here.glob("fixtures/*.py"),
        ]
    )
    untested = [
        f"{path.parent.name}/{path.stem}"
        for path in sorted(
            [*SOURCE_SQL.parent.rglob("*.sql"), *HELM_SQL.rglob("*.sql")]
        )
        if path.stem not in SKIPPED_TEMPLATES
        and not re.search(rf"[\"/]{path.stem}[\"']", source)
    ]

    assert untested == []


FULL: Final = TableState(strategy=Strategy.FULL, signature="s")


TWO_DAYS: Final = (
    "(\"date\" >= '2026-09-23' AND \"date\" < '2026-09-24')"
    " OR (\"date\" >= '2026-09-25' AND \"date\" < '2026-09-26')"
)


RLS_WHERE: Final = "WHERE (unit_id IN ('u1'))"


HOUR_MS: Final = 3_600_000


PARSED_FILTERS: Final = [
    pytest.param(json.dumps(case["expected"]), id=str(case["name"]))
    for case in cast(
        "list[dict[str, object]]", json.loads(PROXY_FILTER_CASES.read_text())
    )
    if case["expected"]
]


CHECK_VIEWS = """
SELECT count(*) FROM duckdb.query(
    'SELECT 1 FROM duckdb_views() WHERE view_name = ''dl_snapshot_check'''
)
"""


async def reconcile_one_table(
    postgres: Postgres,
    monkeypatch: pytest.MonkeyPatch,
    columns: list[tuple[str, str]],
    *,
    fallback: bool = False,
) -> None:
    """Reconcile the views of one table whose BigQuery source reports the given columns."""
    schema = postgres.namespace.schema
    table: TableConfig = (
        PartitionedTable(name=f"p.{schema}.people", fallback=True)
        if fallback
        else FullTable(name=f"p.{schema}.people")
    )

    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})

    async def source_columns(
        _pg_conn: PgBackend, _table: TableConfig
    ) -> list[tuple[str, str]]:
        return columns

    monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)
    await initialize_schemas(postgres.backend, config)
    await reconcile_views(postgres.backend, config)


@pytest.fixture
async def routing(postgres: Postgres) -> Postgres:
    """Install the routing functions in the test transaction."""
    await Executor[PostgresParams, list[TupleRow]](conn=postgres.backend).execute(
        "postgres/views/routing/snapshot",
        mapping={"schema": Identifier(settings.DBOS_APP_SCHEMA)},
    )
    for template in ("coverage", "filter", "plan", "response"):
        await Executor[PostgresParams, list[TupleRow]](conn=postgres.backend).execute(
            f"postgres/views/routing/{template}",
            mapping={"schema": Identifier(settings.DBOS_APP_SCHEMA)},
        )
    return postgres


@pytest.fixture
async def table_function(routing: Postgres) -> Postgres:
    """Install helpers that report the arguments the per-table function passes."""
    await create_access_policy(routing)
    await execute_sql(
        routing,
        "postgres/create_routing_stubs",
        mapping={"schema": routing.namespace.identifier},
    )
    return routing


@pytest.mark.postgres
class TestPostgresTemplates:
    """The PostgreSQL templates, run on the real test server."""

    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            pytest.param(None, None, id="no-header-setting"),
            pytest.param({}, None, id="no-headers"),
            pytest.param({"x-ducklake-snapshot": "42"}, 42, id="numeric"),
            pytest.param({"X-DuckLake-Snapshot": "42"}, 42, id="mixed-case-name"),
        ],
    )
    async def test_requested_snapshot_reads_the_pinned_version(
        self, routing: Postgres, headers: dict[str, str] | None, expected: int | None
    ) -> None:
        """Reads the pinned version."""
        if headers is not None:
            await set_request_headers(routing, headers)

        assert (
            await scalar(routing, "SELECT data_proxy.requested_snapshot()") == expected
        )

    @pytest.mark.parametrize("value", ["abc", "-1", "1.5", "", "1" * 19])
    async def test_requested_snapshot_rejects_a_version_that_is_not_a_whole_number(
        self, routing: Postgres, value: str
    ) -> None:
        """Rejects a version that is not a whole number."""
        await set_request_headers(routing, {"x-ducklake-snapshot": value})

        with pytest.raises(psycopg.Error) as error:
            await routing.connection.execute("SELECT data_proxy.requested_snapshot()")

        assert error.value.sqlstate == "PT400"

    @pytest.mark.parametrize(
        "selection",
        [
            pytest.param(
                TimeRangeSelection(
                    column="date", lower="2026-09-01", upper="2026-09-02"
                ),
                id="time-range",
            ),
            pytest.param(
                RangeSelection(partition_id="10", column="id", lower=10, upper=20),
                id="integer-range",
            ),
            pytest.param(
                RemainderSelection(column="id", start=0, end=100),
                id="remainder",
            ),
        ],
    )
    async def test_selection_condition_renders_the_same_predicate_as_python(
        self,
        routing: Postgres,
        selection: TimeRangeSelection | RangeSelection | RemainderSelection,
    ) -> None:
        """Renders the same predicate as python."""
        rendered = await scalar(
            routing,
            "SELECT data_proxy.selection_condition(%s::jsonb)",
            selection.model_dump_json(),
        )

        assert rendered == selection_condition(selection).as_string(None)

    @pytest.mark.parametrize(
        ("state", "ducklake", "fallback"),
        [
            pytest.param(None, None, "TRUE", id="table-not-in-state"),
            pytest.param(FULL, "TRUE", None, id="full-table"),
            pytest.param(
                partitioned(23, 25),
                TWO_DAYS,
                f"NOT ({TWO_DAYS})",
                id="partitions-with-gap",
            ),
            pytest.param(partitioned(), "FALSE", "NOT (FALSE)", id="no-partitions"),
        ],
    )
    async def test_covered_by_splits_the_table_between_the_sources(
        self,
        routing: Postgres,
        state: TableState | None,
        ducklake: str | None,
        fallback: str | None,
    ) -> None:
        """Splits the table between the sources."""
        await put_state(routing, ROUTED_TABLE, state)

        covered_by_dl = await scalar(
            routing, "SELECT data_proxy.covered_by_dl(%s)", ROUTED_TABLE
        )
        covered_by_fallback = await scalar(
            routing, "SELECT data_proxy.covered_by_fallback(%s)", ROUTED_TABLE
        )

        assert covered_by_dl == ducklake
        assert covered_by_fallback == fallback

    async def test_delete_removed_state_keeps_only_the_listed_tables(
        self, routing: Postgres
    ) -> None:
        """The state and the errors of an unlisted table go, and those of a listed table stay."""
        for table in ("p.d.kept", "p.d.gone"):
            await put_state(routing, table, FULL)
            await emit_error(routing.backend, "table_blocked", table=table)

        await delete_removed_state(routing.backend, {"p.d.kept"})

        app = Identifier(settings.DBOS_APP_SCHEMA)
        states = await routing.connection.execute(
            SQL("SELECT table_name FROM {}.state").format(app)
        )
        errors = await routing.connection.execute(
            SQL("SELECT fields ->> 'table' FROM {}.errors").format(app)
        )
        assert await states.fetchall() == [("p.d.kept",)]
        assert await errors.fetchall() == [("p.d.kept",)]

    async def test_covered_by_reads_state_built_for_a_published_table(
        self, routing: Postgres
    ) -> None:
        """Route a published table from the same key used by the sync writer."""
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name=ROUTED_TABLE)])}
        )
        result = PublicationResult(
            plan=SyncPlan(
                schema_name="app",
                signatures={ROUTED_TABLE: "signature"},
                paths={ROUTED_TABLE: ["s3://bucket/routed.parquet"]},
            ),
            published_tables={ROUTED_TABLE},
        )
        await write_table_states(routing.backend, build_table_states(result, config))

        assert (
            await scalar(
                routing,
                "SELECT data_proxy.covered_by_dl(%s)",
                ROUTED_TABLE,
            )
            == "TRUE"
        )

    @pytest.mark.parametrize(
        (
            "state",
            "fallbacks",
            "pinned",
            "expected",
        ),
        [
            pytest.param(
                partitioned(23),
                ["bigquery"],
                True,
                [("ducklake", True, None)],
                id="pinned-version",
            ),
            pytest.param(
                None,
                ["bigquery"],
                True,
                [("ducklake", True, None)],
                id="pinned-beats-unpublished",
            ),
            pytest.param(
                None,
                ["bigquery"],
                False,
                [("ducklake", False, None), ("bigquery", True, "TRUE")],
                id="unpublished-with-fallback",
            ),
            pytest.param(
                FULL,
                ["bigquery"],
                False,
                [("ducklake", True, None), ("bigquery", False, "FALSE")],
                id="full-table-local",
            ),
            pytest.param(
                FULL,
                [],
                False,
                [("ducklake", True, None)],
                id="full-table-no-fallback",
            ),
            pytest.param(
                partitioned(23, 25),
                ["bigquery"],
                False,
                [("ducklake", True, None), ("bigquery", True, f"NOT ({TWO_DAYS})")],
                id="partitioned-with-fallback",
            ),
            pytest.param(
                partitioned(23, 25),
                [],
                False,
                [("ducklake", True, None)],
                id="partitioned-no-fallback",
            ),
        ],
    )
    async def test_plan_sources_applies_the_first_matching_rule(
        self,
        routing: Postgres,
        state: TableState | None,
        fallbacks: list[str],
        pinned: bool,
        expected: list[tuple[str, bool, str | None]],
    ) -> None:
        """Applies the first matching rule."""
        await put_state(routing, ROUTED_TABLE, state)

        rows = await fetch_all(
            routing,
            "postgres/select_plan_sources",
            mapping={"app_schema": Identifier(settings.DBOS_APP_SCHEMA)},
            params={
                "table_name": ROUTED_TABLE,
                "fallbacks": fallbacks,
                "pinned": pinned,
            },
        )

        assert rows == expected

    async def test_plan_sources_rejects_an_unpublished_table_that_has_no_fallback(
        self, routing: Postgres
    ) -> None:
        """Rejects an unpublished table that has no fallback."""
        await put_state(routing, ROUTED_TABLE, None)

        with pytest.raises(psycopg.Error) as error:
            await fetch_all(
                routing,
                "postgres/select_plan_sources",
                mapping={"app_schema": Identifier(settings.DBOS_APP_SCHEMA)},
                params={
                    "table_name": ROUTED_TABLE,
                    "fallbacks": [],
                    "pinned": False,
                },
            )

        assert error.value.sqlstate == "PT404"

    @pytest.mark.parametrize(
        ("sources", "expected"),
        [
            pytest.param(["ducklake"], "ducklake", id="ducklake"),
            pytest.param(["bigquery"], "bigquery", id="bigquery"),
            pytest.param(["ducklake", "bigquery"], "ducklake+bigquery", id="both"),
            pytest.param([], None, id="none"),
        ],
    )
    async def test_source_label_names_the_sources_queried(
        self,
        routing: Postgres,
        sources: list[str],
        expected: str | None,
    ) -> None:
        """Names the sources queried."""
        label = await scalar(
            routing,
            "SELECT data_proxy.source_label(%s)",
            sources,
        )

        assert label == expected

    @pytest.mark.parametrize(
        ("where", "condition", "expected"),
        [
            pytest.param("", None, "", id="nothing-to-add"),
            pytest.param("WHERE (a)", "TRUE", "WHERE (a)", id="true-condition"),
            pytest.param("", "NOT (b)", "WHERE (NOT (b))", id="no-where-yet"),
            pytest.param(
                "WHERE (a OR c)",
                "NOT (b)",
                "WHERE (a OR c) AND (NOT (b))",
                id="existing-where",
            ),
        ],
    )
    async def test_and_where_adds_a_condition_to_the_where_clause(
        self, routing: Postgres, where: str, condition: str | None, expected: str
    ) -> None:
        """Adds a condition to the where clause."""
        combined = await scalar(
            routing, "SELECT data_proxy.and_where(%s, %s)", where, condition
        )

        assert combined == expected

    @pytest.mark.parametrize(
        ("label", "snapshot", "expected"),
        [
            pytest.param(
                "ducklake",
                7,
                [{"X-Source": "ducklake"}, {"X-DuckLake-Snapshot": "7"}],
                id="source-and-snapshot",
            ),
            pytest.param(
                "bigquery", None, [{"X-Source": "bigquery"}], id="source-only"
            ),
            pytest.param(None, None, [], id="no-headers"),
        ],
    )
    async def test_set_response_headers_sets_the_postgrest_response_headers(
        self,
        routing: Postgres,
        label: str | None,
        snapshot: int | None,
        expected: list[dict[str, str]],
    ) -> None:
        """Sets the postgrest response headers."""
        await routing.connection.execute(
            "SELECT data_proxy.set_response_headers(%s, %s)", (label, snapshot)
        )

        raw = await scalar(routing, "SELECT current_setting('response.headers', true)")
        assert json.loads(str(raw)) == expected

    async def test_view_function_stops_a_denied_user_before_any_source_and_sets_no_headers(
        self, table_function: Postgres
    ) -> None:
        """Stops a denied user before any source and sets no headers."""
        await install_table_function(
            table_function, has_rls=True, fallbacks=["bigquery"]
        )
        await put_state(table_function, ROUTED_TABLE, partitioned(23, 25))
        await set_setting(table_function, "app.claim_sub", "nobody")

        rows, headers = await call_table_function(table_function)

        assert rows == []
        assert headers == {}

    @pytest.mark.parametrize(
        ("role", "schemas", "outcome"),
        [
            pytest.param("dp_api", None, "rows", id="api-role"),
            pytest.param("dp_console", None, "rows", id="console-role-inherits-api"),
            pytest.param("dp_admin", None, "rows", id="admin-role-inherits-api"),
            pytest.param("dp_policy", None, "refused", id="policy-role-lacks-read-api"),
            pytest.param("dp_ghost", None, "refused", id="role-the-file-does-not-know"),
            pytest.param("", None, "refused", id="no-role"),
            pytest.param("dp_api", "elsewhere", "empty", id="schema-outside-the-claim"),
            pytest.param("dp_api", "", "empty", id="empty-schemas-claim"),
        ],
    )
    async def test_view_function_needs_read_api_and_the_schema_in_the_claim(
        self, table_function: Postgres, role: str, schemas: str | None, outcome: str
    ) -> None:
        """A role without read_api is refused, a schema outside the claim returns nothing, and the others read."""
        await install_table_function(table_function, has_rls=False, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)

        if outcome == "refused":
            with pytest.raises(psycopg.Error) as error:
                await call_table_function(table_function, role, schemas)
            assert error.value.sqlstate == "42501"
            return

        rows, _ = await call_table_function(table_function, role, schemas)

        assert rows == ([("ducklake", "", "7")] if outcome == "rows" else [])

    @pytest.mark.parametrize(
        ("claim", "workflow", "partition", "expected"),
        [
            pytest.param("s1", "w1", None, True, id="scope-row-in-the-claim"),
            pytest.param("s2", "w1", None, False, id="scope-row-outside-the-claim"),
            pytest.param("s2", "w9", "s2", True, id="partition-key-in-the-claim"),
            pytest.param("s1", "w9", None, False, id="no-row-and-no-partition"),
            pytest.param("s1", "w9", "s2", False, id="partition-key-outside-the-claim"),
            pytest.param("", "w1", None, False, id="empty-claim"),
        ],
    )
    async def test_sees_a_workflow_by_its_partition_or_by_the_schemas_of_its_scope(
        self,
        postgres: Postgres,
        claim: str,
        workflow: str,
        partition: str | None,
        expected: bool,
    ) -> None:
        """A token sees a workflow when its queue partition or one of its recorded schemas is in the claim."""
        await postgres.connection.execute(
            SQL(
                "INSERT INTO {}.scope (workflow_id, run_id, schema_name) VALUES ('w1', 'r1', 's1')"
            ).format(Identifier(settings.DBOS_APP_SCHEMA))
        )
        await act_as_token(postgres, "dp_console", claim)

        assert (
            await scalar(
                postgres,
                SQL("SELECT {}.sees(%s, %s)")
                .format(Identifier(settings.DBOS_APP_SCHEMA))
                .as_string(),
                workflow,
                partition,
            )
            is expected
        )

    @pytest.mark.parametrize(
        ("state", "fallbacks", "pinned", "sources", "label", "snapshot"),
        [
            pytest.param(
                FULL, ["bigquery"], None, ["ducklake"], "ducklake", "7", id="full-table"
            ),
            pytest.param(
                None,
                ["bigquery"],
                None,
                ["bigquery"],
                "bigquery",
                None,
                id="unpublished",
            ),
            pytest.param(
                partitioned(23, 25),
                ["bigquery"],
                None,
                ["bigquery", "ducklake"],
                "ducklake+bigquery",
                "7",
                id="partitioned-with-fallback",
            ),
            pytest.param(
                partitioned(23, 25),
                [],
                None,
                ["ducklake"],
                "ducklake",
                "7",
                id="fallback-disabled",
            ),
            pytest.param(
                partitioned(23, 25),
                ["bigquery"],
                "5",
                ["ducklake"],
                "ducklake",
                "5",
                id="pinned-version",
            ),
        ],
    )
    async def test_view_function_calls_only_the_planned_helpers(
        self,
        table_function: Postgres,
        state: TableState | None,
        fallbacks: list[str],
        pinned: str | None,
        sources: list[str],
        label: str,
        snapshot: str | None,
    ) -> None:
        """Calls only the planned helpers."""
        await install_table_function(table_function, has_rls=True, fallbacks=fallbacks)
        await put_state(table_function, ROUTED_TABLE, state)
        await grant_unit(table_function, "u")
        if pinned is not None:
            await set_request_headers(table_function, {"x-ducklake-snapshot": pinned})

        rows, headers = await call_table_function(table_function)

        assert [row[0] for row in rows] == sources
        assert headers.get("X-Source") == label
        assert headers.get("X-DuckLake-Snapshot") == snapshot

    async def test_view_function_passes_the_rls_predicate_and_the_bigquery_coverage(
        self, table_function: Postgres
    ) -> None:
        """Passes the rls predicate and the bigquery coverage."""
        await install_table_function(
            table_function, has_rls=True, fallbacks=["bigquery"]
        )
        await put_state(table_function, ROUTED_TABLE, partitioned(23, 25))
        await grant_unit(table_function, "u")

        rows, _ = await call_table_function(table_function)

        assert rows == [
            ("bigquery", RLS_WHERE, f"NOT ({TWO_DAYS})"),
            ("ducklake", RLS_WHERE, "7"),
        ]

    async def test_view_function_passes_an_empty_predicate_without_rls(
        self, table_function: Postgres
    ) -> None:
        """Passes an empty predicate without rls."""
        await install_table_function(table_function, has_rls=False, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)

        rows, _ = await call_table_function(table_function)

        assert rows == [("ducklake", "", "7")]

    async def test_view_function_an_empty_ducklake_result_never_calls_bigquery(
        self, table_function: Postgres
    ) -> None:
        """An empty ducklake result never calls bigquery."""
        await install_table_function(
            table_function, has_rls=True, fallbacks=["bigquery"]
        )
        await put_state(table_function, ROUTED_TABLE, FULL)
        await grant_unit(table_function, "u")
        await set_setting(table_function, "test.dl_empty", "on")

        rows, headers = await call_table_function(table_function)

        assert rows == []
        assert headers["X-Source"] == "ducklake"

    async def _install_filter(self, postgres: Postgres) -> None:
        """Install the routing filter function under test."""
        await postgres.connection.execute(
            render_template(
                "postgres/views/routing/filter",
                {"schema": Identifier(settings.DBOS_APP_SCHEMA)},
            )
        )

    async def test_filter_renders_where_nodes(self, postgres: Postgres) -> None:
        """Renders where nodes."""
        await self._install_filter(postgres)

        cases = [
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "gte",
                        "negated": False,
                        "value": "10",
                    }
                },
                "(\"id\" >= '10')",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "in",
                        "negated": False,
                        "value": ["1", "2"],
                    }
                },
                "(\"id\" IN ('1', '2'))",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "name",
                        "operator": "ilike",
                        "negated": False,
                        "value": "*rio*",
                    }
                },
                "(\"name\" ILIKE '%rio%')",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "eq",
                        "negated": True,
                        "value": "1",
                    }
                },
                "NOT ((\"id\" = '1'))",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "is",
                        "negated": False,
                        "value": "not_null",
                    }
                },
                '("id" IS NOT NULL)',
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "is",
                        "negated": False,
                        "value": "unknown",
                    }
                },
                '("id" IS UNKNOWN)',
            ),
            (
                {
                    "where": {
                        "kind": "group",
                        "operator": "or",
                        "items": [
                            {
                                "kind": "comparison",
                                "column": "id",
                                "operator": "eq",
                                "negated": False,
                                "value": "1",
                            },
                            {
                                "kind": "comparison",
                                "column": "name",
                                "operator": "eq",
                                "negated": False,
                                "value": "x",
                            },
                        ],
                    }
                },
                "((\"id\" = '1') OR (\"name\" = 'x'))",
            ),
        ]

        for payload, expected in cases:
            rendered = await scalar(
                postgres,
                "SELECT data_proxy.render_dl_where(%s, ARRAY['id', 'name']::text[])",
                json.dumps(payload),
            )
            assert rendered == expected

    @pytest.mark.parametrize("document", PARSED_FILTERS)
    async def test_filter_renders_every_document_that_the_proxy_parser_builds(
        self, postgres: Postgres, document: str
    ) -> None:
        """Renders each document in the proxy parser fixtures and quotes its columns."""
        await self._install_filter(postgres)
        columns = set(re.findall(r'"column": "([^"]+)"', document))

        rendered = await scalar(
            postgres,
            "SELECT data_proxy.render_dl_where(%s, %s::text[])",
            document,
            sorted(columns),
        )

        assert isinstance(rendered, str)
        assert all(f'"{column}"' in rendered for column in columns)

    async def test_filter_rejects_unknown_columns_and_operators(
        self, postgres: Postgres
    ) -> None:
        """Rejects unknown columns and operators."""
        await self._install_filter(postgres)

        unknown_column = {
            "where": {
                "kind": "comparison",
                "column": "nope",
                "operator": "eq",
                "negated": False,
                "value": "1",
            }
        }
        with pytest.raises(psycopg.Error) as error:
            async with postgres.connection.transaction():
                await scalar(
                    postgres,
                    "SELECT data_proxy.render_dl_where(%s, ARRAY['id']::text[])",
                    json.dumps(unknown_column),
                )
        assert error.value.sqlstate == "PT400"

        unknown_operator = {
            "where": {
                "kind": "comparison",
                "column": "id",
                "operator": "fts",
                "negated": False,
                "value": "x",
            }
        }
        with pytest.raises(psycopg.Error) as error:
            async with postgres.connection.transaction():
                await scalar(
                    postgres,
                    "SELECT data_proxy.render_dl_where(%s, ARRAY['id']::text[])",
                    json.dumps(unknown_operator),
                )
        assert error.value.sqlstate == "PT400"

    async def test_filter_request_filter_reads_the_header(
        self, postgres: Postgres
    ) -> None:
        """Request filter reads the header."""
        await self._install_filter(postgres)

        assert await scalar(postgres, "SELECT data_proxy.request_filter()") is None

        payload = {
            "where": {
                "kind": "comparison",
                "column": "id",
                "operator": "eq",
                "negated": False,
                "value": "1",
            }
        }
        await set_setting(
            postgres,
            "request.headers",
            json.dumps({"x-ducklake-filter": json.dumps(payload)}),
        )
        assert await scalar(postgres, "SELECT data_proxy.request_filter()") == payload

        await set_setting(
            postgres, "request.headers", json.dumps({"x-ducklake-filter": "not-json"})
        )
        with pytest.raises(psycopg.Error) as error:
            await postgres.connection.execute("SELECT data_proxy.request_filter()")
        assert error.value.sqlstate == "PT400"

    async def test_cleanup_stale_objects_drops_only_views_missing_from_the_config(
        self, postgres: Postgres, psql: Psql
    ) -> None:
        """Drops only views missing from the config."""
        schema = postgres.namespace.schema
        target = postgres.namespace.identifier
        psql.run(
            psql_script(
                helm_sql("cleanup_stale_objects", {"schema": target}),
                render_template(
                    "postgres/create_view",
                    {"schema": target, "view": Identifier("kept")},
                    root=TEST_SQL_DIR,
                ),
                render_template(
                    "postgres/create_view",
                    {"schema": target, "view": Identifier("stale")},
                    root=TEST_SQL_DIR,
                ),
                render_template(
                    "postgres/create_stub_function",
                    {"schema": target, "function": Identifier("stale_fn")},
                    root=TEST_SQL_DIR,
                ),
                helm_sql(
                    "call_cleanup_stale_objects",
                    {"schema": target, "schema_argument": Literal(schema)},
                ),
            ),
            {
                "config": json.dumps(
                    {"schemas": {schema: {"tables": [{"name": f"p.{schema}.kept"}]}}}
                )
            },
        )

        assert [
            await relation_exists(postgres, schema, "kept"),
            await relation_exists(postgres, schema, "stale"),
            await function_exists(postgres, schema, "stale_fn()"),
        ] == [True, False, False]

    async def test_prune_access_log_keeps_only_recent_rows(
        self, postgres: Postgres, psql: Psql
    ) -> None:
        """Keeps only recent rows."""
        schema = postgres.namespace.schema
        target = postgres.namespace.identifier
        psql.run(
            psql_script(
                helm_sql(
                    "setup_access_policy",
                    {
                        "schema": target,
                        "user_role": Identifier(settings.AUTH_USER_ROLE),
                        "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
                        "scope": schema_scope_condition(schema),
                    },
                ),
                helm_sql("prune_access_log", {"schema": target}),
            )
        )
        for subject, age in (("stale", "100 days"), ("recent", "1 day")):
            await execute_sql(
                postgres,
                "postgres/insert_access_log",
                mapping={"schema": target},
                params={
                    "subject": subject,
                    "unit_type": "unit",
                    "unit_id": "unit_1",
                    "action": "insert",
                    "age": age,
                },
            )
        await postgres.connection.commit()

        psql.run(
            psql_script(helm_sql("call_prune_access_log", {"schema": target})),
            {"retention": "90 days", "schema": schema},
        )

        assert await fetch_all(
            postgres,
            "postgres/access_log_entries",
            mapping={"schema": target},
        ) == [("recent", "unit", "unit_1", "insert")]

    async def test_recover_orphaned_workflows_re_enqueues_only_orphaned_workflows(
        self, postgres: Postgres, psql: Psql, dbos_schema: str
    ) -> None:
        """Re enqueues only orphaned workflows."""
        schema = postgres.namespace.schema
        dbos = {"dbos_schema": Identifier(dbos_schema)}
        prefix = f"{schema}-"
        now_ms = int(time() * 1000)
        workflows = {
            "orphaned": (schema, "q", "gone", HOUR_MS),
            "live-executor": (schema, "q", "live", HOUR_MS),
            "within-grace": (schema, "q", "gone", 0),
            "other-application": (f"{schema}_other", "q", "gone", HOUR_MS),
            "not-queued": (schema, None, "gone", HOUR_MS),
        }
        try:
            for name, (application, queue, executor, age_ms) in workflows.items():
                await execute_sql(
                    postgres,
                    "postgres/insert_workflow",
                    mapping=dbos,
                    params={
                        "workflow_uuid": f"{prefix}{name}",
                        "application_name": application,
                        "queue_name": queue,
                        "executor_id": executor,
                        "started_at_epoch_ms": now_ms - age_ms,
                    },
                )
            await postgres.connection.commit()

            psql.run(
                psql_script(
                    helm_sql(
                        "recover_orphaned_workflows",
                        {
                            "schema": postgres.namespace.identifier,
                            "application_name": Literal(schema),
                        },
                    ),
                    helm_sql(
                        "call_recover_orphaned_workflows",
                        {
                            "schema": postgres.namespace.identifier,
                            "live_executor_ids": Literal(json.dumps(["live"])),
                            "grace_seconds": "60",
                        },
                    ),
                )
            )

            assert await fetch_all(
                postgres,
                "postgres/select_workflows",
                mapping=dbos,
                params={"prefix": prefix},
            ) == [
                (f"{prefix}live-executor", "PENDING", False),
                (f"{prefix}not-queued", "PENDING", False),
                (f"{prefix}orphaned", "ENQUEUED", True),
                (f"{prefix}other-application", "PENDING", False),
                (f"{prefix}within-grace", "PENDING", False),
            ]
        finally:
            await postgres.connection.rollback()
            await execute_sql(
                postgres,
                "postgres/delete_workflows",
                mapping=dbos,
                params={"prefix": prefix},
            )
            await postgres.connection.commit()

    @pytest.mark.parametrize(
        ("pinned", "rows", "snapshot"),
        [
            pytest.param(None, 20, "2", id="latest-snapshot"),
            pytest.param("1", 10, "1", id="pinned-snapshot"),
        ],
    )
    async def test_view_function_snapshot_reads_the_requested_snapshot(
        self, ducklake_view: Postgres, pinned: str | None, rows: int, snapshot: str
    ) -> None:
        """Reads the requested snapshot."""
        if pinned is not None:
            await set_request_headers(ducklake_view, {"x-ducklake-snapshot": pinned})

        count = await scalar(
            ducklake_view,
            f'SELECT count(*) FROM "{ducklake_view.namespace.schema}".people',
        )

        assert count == rows
        assert await response_headers(ducklake_view) == {
            "X-Source": "ducklake",
            "X-DuckLake-Snapshot": snapshot,
        }

    async def test_view_function_snapshot_rejects_a_pinned_snapshot_that_does_not_exist(
        self, ducklake_view: Postgres
    ) -> None:
        """Rejects a pinned snapshot that does not exist."""
        await set_request_headers(ducklake_view, {"x-ducklake-snapshot": "99"})

        with pytest.raises(psycopg.Error) as error:
            await ducklake_view.connection.execute(
                f'SELECT * FROM "{ducklake_view.namespace.schema}".people'.encode()
            )

        assert error.value.sqlstate == "PT404"

    @pytest.mark.parametrize(
        ("pinned", "checked"),
        [
            pytest.param(None, False, id="latest-snapshot-needs-no-check"),
            pytest.param("1", True, id="pinned-snapshot-is-checked"),
        ],
    )
    async def test_view_function_snapshot_checks_the_snapshot_only_when_the_request_pins_it(
        self, ducklake_view: Postgres, pinned: str | None, checked: bool
    ) -> None:
        """Checks the snapshot only when the request pins it."""
        if pinned is not None:
            await set_request_headers(ducklake_view, {"x-ducklake-snapshot": pinned})
        await scalar(
            ducklake_view,
            f'SELECT count(*) FROM "{ducklake_view.namespace.schema}".people',
        )

        views = await scalar(ducklake_view, CHECK_VIEWS)

        assert views == int(checked)

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            pytest.param(2, None, [(2, "insert", 10)], id="since-snapshot-to-latest"),
            pytest.param(
                1, 2, [(1, "insert", 10), (2, "insert", 10)], id="snapshot-range"
            ),
        ],
    )
    async def test_table_changes_function_returns_the_changed_rows(
        self,
        ducklake_view: Postgres,
        start: int,
        end: int | None,
        expected: list[tuple[int, str, int]],
    ) -> None:
        """Returns the changed rows, to a token whose role holds read_ducklake."""
        await act_as_token(ducklake_view, "dp_admin", ducklake_view.namespace.schema)
        rows = await fetch_all(
            ducklake_view,
            "postgres/select_changes",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"start_snapshot": start, "end_snapshot": end},
        )

        assert rows == expected

    async def test_table_changes_function_refuses_a_role_without_read_ducklake(
        self, ducklake_view: Postgres
    ) -> None:
        """The api role reads data but not the change feed of the catalog."""
        await act_as_token(ducklake_view, "dp_api", ducklake_view.namespace.schema)

        with pytest.raises(psycopg.Error) as error:
            await fetch_all(
                ducklake_view,
                "postgres/select_changes",
                mapping={"schema": ducklake_view.namespace.identifier},
                params={"start_snapshot": 1, "end_snapshot": 2},
            )

        assert error.value.sqlstate == "42501"

    async def test_table_files_function_lists_the_data_files_of_a_table(
        self, ducklake_view: Postgres
    ) -> None:
        """Each file has a path in the data location of the schema, and a size."""
        schema = ducklake_view.namespace.schema

        files = await fetch_all(
            ducklake_view,
            "postgres/select_table_files",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"table_name": "people"},
        )

        assert files
        assert all(
            str(path).endswith(".parquet") and "/" + schema + "/" in str(path)
            for path, _, _ in files
        )
        assert all(isinstance(size, int) and size > 0 for _, size, _ in files)

    async def test_table_files_function_lists_nothing_for_an_unknown_table(
        self, ducklake_view: Postgres
    ) -> None:
        """A table name that the catalog doesn't know gives no file."""
        files = await fetch_all(
            ducklake_view,
            "postgres/select_table_files",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"table_name": "nothing"},
        )

        assert files == []

    async def test_current_snapshot_function_snapshot_function_returns_the_latest_snapshot(
        self, ducklake_view: Postgres
    ) -> None:
        """Snapshot function returns the latest snapshot."""
        schema = ducklake_view.namespace.schema

        assert (
            await scalar(ducklake_view, f'SELECT "{schema}".dl_latest_snapshot()') == 2
        )

    async def test_serving_functions_of_two_schemas_attach_one_catalog_each(
        self, ducklake_view: Postgres
    ) -> None:
        """One backend serves several schemas, so each schema attaches its catalog under its own alias."""
        schema = ducklake_view.namespace.schema
        other = f"{schema}_other"
        publish_people_catalog(
            other, f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{other}"
        )
        await ducklake_view.connection.execute(
            render_template(
                "postgres/create_current_snapshot_function",
                {
                    "schema": ducklake_view.namespace.identifier,
                    "function": Identifier("dl_latest_snapshot_other"),
                    "metadata_schema": other,
                    "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{other}",
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                },
            ).encode()
        )

        await scalar(ducklake_view, f'SELECT "{schema}".dl_latest_snapshot()')
        await scalar(ducklake_view, f'SELECT "{schema}".dl_latest_snapshot_other()')

        cursor = await ducklake_view.connection.execute(
            b"""
            SELECT r['database_name']::text
            FROM duckdb.query(
                'SELECT database_name FROM duckdb_databases() WHERE database_name LIKE ''dl_%'' ORDER BY 1'
            ) r
            """
        )
        assert [name for (name,) in await cursor.fetchall()] == sorted(
            [f"dl_{schema}", f"dl_{other}"]
        )

    async def test_create_state_makes_the_state_error_and_scope_tables(
        self, postgres: Postgres
    ) -> None:
        """Create the three tables of the engine, and create them again without error."""
        statement = helm_sql(
            "create_state", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
        ).encode()

        await postgres.connection.execute(statement)
        await postgres.connection.execute(statement)

        assert (
            await scalar(
                postgres,
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name IN ('state', 'errors', 'scope')",
                settings.DBOS_APP_SCHEMA,
            )
            == 3
        )

    async def test_create_schema_makes_the_schema_once(
        self, postgres: Postgres
    ) -> None:
        """Create a schema, and create it again without error."""
        name = f"{postgres.namespace.schema}_new"
        statement = helm_sql("create_schema", {"schema": Identifier(name)}).encode()

        await postgres.connection.execute(statement)
        await postgres.connection.execute(statement)

        assert (
            await scalar(
                postgres, "SELECT count(*) FROM pg_namespace WHERE nspname = %s", name
            )
            == 1
        )

    async def test_remove_schema_drops_the_schema_and_what_the_engine_kept_for_it(
        self, postgres: Postgres
    ) -> None:
        """The state, the scope and the errors of a removed schema go with it, and the other schemas keep theirs."""
        name = f"{postgres.namespace.schema}_gone"
        app = Identifier(settings.DBOS_APP_SCHEMA)
        await create_state_tables(postgres.connection)
        await postgres.connection.execute(
            SQL(
                """
                CREATE SCHEMA {gone};
                CREATE VIEW {gone}.people AS SELECT 1 AS id;
                CREATE TABLE {gone}.access_policy (subject text);
                INSERT INTO {app}.state VALUES
                    ('p.d.people', '{{}}'), ('p.d.other', '{{}}');
                INSERT INTO {app}.scope VALUES
                    ('w1', 'r1', {name}, ''), ('w2', 'r2', 'kept', '');
                INSERT INTO {app}.errors (reason, fields) VALUES
                    ('table_blocked', '{{"table": "p.d.people"}}'),
                    ('table_blocked', '{{"table": "p.d.other"}}')
                """
            ).format(gone=Identifier(name), app=app, name=Literal(name))
        )
        statement = helm_sql(
            "remove_schema",
            {"schema": Identifier(name), "name": Literal(name), "app_schema": app},
        ).encode()

        await postgres.connection.execute(statement)
        await postgres.connection.execute(statement)

        assert (
            await scalar(
                postgres, "SELECT count(*) FROM pg_namespace WHERE nspname = %s", name
            )
            == 0
        )
        cursor = await postgres.connection.execute(
            SQL(
                """
                SELECT
                    (SELECT array_agg(table_name) FROM {app}.state
                     WHERE table_name IN ('p.d.people', 'p.d.other')),
                    (SELECT array_agg(schema_name) FROM {app}.scope
                     WHERE workflow_id IN ('w1', 'w2')),
                    (SELECT array_agg(fields ->> 'table') FROM {app}.errors
                     WHERE fields ->> 'table' IN ('p.d.people', 'p.d.other'))
                """
            ).format(app=app)
        )
        assert await cursor.fetchone() == (["p.d.other"], ["kept"], ["p.d.other"])

    async def test_drop_catalog_schema_drops_the_metadata_schema_and_is_repeatable(
        self, postgres: Postgres
    ) -> None:
        """The catalog database keeps one metadata schema for each application schema."""
        name = f"{postgres.namespace.schema}_catalog"
        await postgres.connection.execute(
            SQL(
                "CREATE SCHEMA {}; CREATE TABLE {}.ducklake_snapshot (id integer)"
            ).format(Identifier(name), Identifier(name))
        )
        statement = helm_sql(
            "drop_catalog_schema", {"schema": Identifier(name)}
        ).encode()

        await postgres.connection.execute(statement)
        await postgres.connection.execute(statement)

        assert (
            await scalar(
                postgres, "SELECT count(*) FROM pg_namespace WHERE nspname = %s", name
            )
            == 0
        )

    @pytest.mark.parametrize(
        ("claims", "expected"),
        [
            pytest.param(
                {"sub": "u1", "schemas": ["a", "b"]},
                {"app.claim_sub": "u1", "app.claim_schemas": "a,b"},
                id="string-and-array-claims",
            ),
            pytest.param(
                {"preferred_username": "test_user_1", "level": 5},
                {"app.claim_preferred_username": "test_user_1", "app.claim_level": "5"},
                id="string-and-number-claims",
            ),
            pytest.param(None, {"app.claim_sub": None}, id="no-claims"),
            pytest.param(
                {"sub": "u1", "allowed-origins": ["http://console.localhost"]},
                {"app.claim_sub": "u1"},
                id="claim-whose-name-is-not-a-setting-name",
            ),
        ],
    )
    async def test_pre_request_maps_each_claim_to_an_app_setting(
        self,
        rbac: Postgres,
        claims: dict[str, str | int | list[str]] | None,
        expected: dict[str, str | None],
    ) -> None:
        """Copy every claim of the token into a setting of the transaction."""
        postgres = rbac
        await postgres.connection.execute(
            helm_sql(
                "create_pre_request", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
            ).encode()
        )
        if claims is not None:
            await set_setting(postgres, "request.jwt.claims", json.dumps(claims))

        await postgres.connection.execute("SELECT data_proxy.pre_request()")

        assert {
            name: await scalar(postgres, "SELECT current_setting(%s, true)", name)
            for name in expected
        } == expected

    @pytest.mark.parametrize(
        ("claims", "can_read_sync", "can_start_sync", "mine_pic"),
        [
            pytest.param(
                {"role": "dp_console", "schemas": ["pic"]},
                True,
                False,
                True,
                id="console-of-one-schema",
            ),
            pytest.param(
                {"role": "dp_admin", "schemas": ["pic", "brt"]},
                True,
                True,
                True,
                id="admin-of-two-schemas",
            ),
            pytest.param(
                {"role": "dp_admin", "schemas": ["brt"]},
                True,
                True,
                False,
                id="admin-of-another-schema",
            ),
            pytest.param(
                {"role": "dp_ghost", "schemas": ["pic"]},
                False,
                False,
                True,
                id="unknown-role",
            ),
            pytest.param(None, False, False, False, id="no-token"),
        ],
    )
    async def test_pre_request_loads_the_rules_for_the_role_and_schemas_of_the_token(
        self,
        rbac: Postgres,
        claims: dict[str, str | list[str]] | None,
        can_read_sync: bool,
        can_start_sync: bool,
        mine_pic: bool,
    ) -> None:
        """After the pre-request function, the checks answer for the role and the schemas of the token."""
        await rbac.connection.execute(
            helm_sql(
                "create_pre_request", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
            ).encode()
        )
        if claims is not None:
            await set_setting(rbac, "request.jwt.claims", json.dumps(claims))

        await rbac.connection.execute("SELECT data_proxy.pre_request()")

        assert [
            await scalar(rbac, "SELECT rbac.can('read_sync')"),
            await scalar(rbac, "SELECT rbac.can('start_sync')"),
            await scalar(rbac, "SELECT rbac.mine('pic')"),
        ] == [can_read_sync, can_start_sync, mine_pic]

    @pytest.mark.parametrize(
        ("profile", "longer"),
        [
            pytest.param("console", True, id="console-profile"),
            pytest.param("sales", False, id="data-profile"),
        ],
    )
    async def test_pre_request_gives_the_console_profile_a_longer_timeout(
        self, rbac: Postgres, profile: str, longer: bool
    ) -> None:
        """A request to the console reads views over the engine tables, so it may run for 30 seconds."""
        postgres = rbac
        await postgres.connection.execute(
            helm_sql(
                "create_pre_request", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
            ).encode()
        )
        before = await scalar(postgres, "SELECT current_setting('statement_timeout')")
        await set_request_headers(postgres, {"accept-profile": profile})

        await postgres.connection.execute("SELECT data_proxy.pre_request()")

        after = await scalar(postgres, "SELECT current_setting('statement_timeout')")
        assert after == ("30s" if longer else before)

    async def test_create_reload_notifies_postgrest_and_does_not_wait_without_a_standby(
        self, postgres: Postgres
    ) -> None:
        """A listener on the pgrst channel gets the message at once when no standby streams."""
        async with (
            await AsyncConnection.connect(postgres.dsn, autocommit=True) as writer,
            await AsyncConnection.connect(postgres.dsn, autocommit=True) as listener,
        ):
            await writer.execute(
                helm_sql(
                    "create_reload", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
                ).encode()
            )
            await listener.execute(b"LISTEN pgrst")
            started = perf_counter()

            await notify_postgrest(PgBackend(connection=writer))
            await wait_for_standbys(PgBackend(connection=writer))

            async with timeout(2):
                async for notification in listener.notifies():
                    assert notification.payload == "reload schema"
                    break
            assert perf_counter() - started < 1

    async def test_setup_access_policy_logs_every_change(
        self, postgres: Postgres
    ) -> None:
        """The trigger writes the old row of an update and a delete, and the new row of an insert."""
        await create_access_policy(postgres)
        table = postgres.namespace.identifier

        await insert_access_policy(postgres, "ana", "unit", "u1")
        await postgres.connection.execute(
            SQL("UPDATE {}.access_policy SET unit_id = 'u2'").format(table)
        )
        await postgres.connection.execute(
            SQL("DELETE FROM {}.access_policy").format(table)
        )

        assert sorted(
            await fetch_all(
                postgres, "postgres/access_log_entries", mapping={"schema": table}
            )
        ) == [
            ("ana", "unit", "u1", "insert"),
            ("ana", "unit", "u1", "update"),
            ("ana", "unit", "u2", "delete"),
        ]

    @pytest.mark.parametrize(
        ("role", "claim", "expected"),
        [
            pytest.param("dp_policy", "{schema}", 1, id="policy-role-of-the-schema"),
            pytest.param("dp_policy", "other", 0, id="policy-role-of-another-schema"),
            pytest.param("dp_api", "{schema}", 0, id="role-without-read-policy"),
        ],
    )
    async def test_setup_access_policy_shows_the_log_to_a_token_that_may_read_the_policy(
        self, rbac: Postgres, role: str, claim: str, expected: int
    ) -> None:
        """A token reads the access log of a schema when it holds read_policy for that schema."""
        await create_access_policy(rbac)
        await insert_access_policy(rbac, "ana", "unit", "u1")
        await act_as_token(rbac, role, claim.format(schema=rbac.namespace.schema))
        token_role = Identifier(settings.AUTH_USER_ROLE)
        await rbac.connection.execute(
            SQL(
                "GRANT USAGE ON SCHEMA rbac TO {role}; GRANT EXECUTE ON FUNCTION rbac.can(text), rbac.mine(text) TO {role}; SET LOCAL ROLE {role}"
            ).format(role=token_role)
        )

        assert (
            await scalar(
                rbac,
                SQL("SELECT count(*) FROM {}.access_log")
                .format(rbac.namespace.identifier)
                .as_string(),
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("statement", "expected"),
        [
            pytest.param(
                "INSERT INTO console.schemas VALUES ('a')",
                ["provision_schemas", "reload_proxy"],
                id="schema-added",
            ),
            pytest.param(
                "UPDATE console.schemas SET name = name",
                ["provision_schemas", "reload_proxy"],
                id="schema-changed",
            ),
            pytest.param(
                "DELETE FROM console.schemas",
                ["provision_schemas", "reload_proxy"],
                id="schema-removed",
            ),
            pytest.param(
                "INSERT INTO console.tables VALUES ('t')",
                ["reload_proxy"],
                id="table-added",
            ),
            pytest.param(
                "DELETE FROM console.tables",
                ["cleanup_tables", "reload_proxy"],
                id="table-removed",
            ),
            pytest.param(
                "UPDATE rbac.revision SET value = value + 1",
                ["reload_proxy"],
                id="rules-or-policy-revision-moved",
            ),
            pytest.param(
                "INSERT INTO {policy} VALUES ('ana', 'unit', 'u1') ON CONFLICT DO NOTHING",
                ["reload_proxy"],
                id="policy-written",
            ),
        ],
    )
    async def test_create_console_triggers_queues_the_workflow_that_the_change_needs(
        self,
        rbac: Postgres,
        dbos_schema: str,
        statement: LiteralString,
        expected: list[str],
    ) -> None:
        """A change queues each workflow once, so that a burst of changes shares one run."""
        await create_access_policy(rbac)
        await install_console_triggers(rbac)
        sql = SQL(statement).format(
            policy=SQL("{}.access_policy").format(rbac.namespace.identifier)
        )

        before = await queued_workflows(rbac, dbos_schema)
        await rbac.connection.execute(sql)
        await rbac.connection.execute(sql)

        queued = await queued_workflows(rbac, dbos_schema)
        assert sorted(set(queued) - set(before)) == expected
        assert len(queued) - len(before) == len(expected)

    async def test_create_console_triggers_queues_the_next_run_while_one_is_running(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """A change that arrives during a run waits for the next run, because the running one may have read the data already."""
        await install_console_triggers(rbac)
        insert = SQL("INSERT INTO console.schemas VALUES ('a')")
        await rbac.connection.execute(insert)
        await rbac.connection.execute(
            SQL(
                "UPDATE {}.workflow_status SET status = 'PENDING' WHERE name = 'provision_schemas' AND status = 'ENQUEUED'"
            ).format(Identifier(dbos_schema))
        )

        await rbac.connection.execute(insert)
        await rbac.connection.execute(insert)

        cursor = await rbac.connection.execute(
            SQL(
                "SELECT status FROM {}.workflow_status WHERE name = 'provision_schemas' ORDER BY status"
            ).format(Identifier(dbos_schema))
        )
        assert [status for (status,) in await cursor.fetchall()] == [
            "ENQUEUED",
            "PENDING",
        ]

    async def test_setup_access_policy_raises_the_revision_once_for_each_write_statement(
        self, rbac: Postgres
    ) -> None:
        """The proxy learns of a policy change from the revision, so each write statement moves it by one."""
        await create_access_policy(rbac)
        table = rbac.namespace.identifier
        revisions = [await revision(rbac)]

        for statement in (
            "INSERT INTO {} VALUES ('ana', 'unit', 'u1'), ('bia', 'unit', 'u2')",
            "UPDATE {} SET unit_id = 'u3'",
            "DELETE FROM {}",
        ):
            await rbac.connection.execute(
                SQL(statement).format(SQL("{}.access_policy").format(table))
            )
            revisions.append(await revision(rbac))

        assert [after - before for before, after in itertools.pairwise(revisions)] == [
            1,
            1,
            1,
        ]

    async def test_setup_access_policy_makes_a_policy_write_wait_for_the_standbys(
        self, postgres: Postgres
    ) -> None:
        """A policy write raises synchronous_commit for its own transaction only."""
        await create_access_policy(postgres)

        before = await scalar(postgres, "SELECT current_setting('synchronous_commit')")
        await insert_access_policy(postgres, "ana", "unit", "u1")
        during = await scalar(postgres, "SELECT current_setting('synchronous_commit')")
        await postgres.connection.rollback()
        after = await scalar(postgres, "SELECT current_setting('synchronous_commit')")

        assert (before, during, after) == ("on", "remote_apply", "on")

    async def test_every_security_definer_function_pins_its_search_path(
        self, table_function: Postgres
    ) -> None:
        """A function that runs with its owner rights must not trust the search_path of the caller."""
        await install_table_function(
            table_function, has_rls=True, fallbacks=["bigquery"]
        )
        await install_rbac(table_function)
        for name in (
            "create_state",
            "create_reload",
            "create_pre_request",
            "cleanup_stale_objects",
            "prune_access_log",
            "recover_orphaned_workflows",
        ):
            await table_function.connection.execute(
                helm_sql(
                    name,
                    {
                        "schema": Identifier(settings.DBOS_APP_SCHEMA),
                        "application_name": Literal("app"),
                    },
                ).encode()
            )

        cursor = await table_function.connection.execute(
            """
            SELECT p.oid::regprocedure::text
            FROM pg_proc AS p
            INNER JOIN pg_namespace AS n ON p.pronamespace = n.oid
            WHERE n.nspname = ANY(%s) AND p.prosecdef AND NOT EXISTS (
                SELECT FROM unnest(coalesce(p.proconfig, '{}')) AS c
                WHERE c LIKE 'search_path=pg_catalog%%'
            )
            """,
            ([table_function.namespace.schema, settings.DBOS_APP_SCHEMA, "rbac"],),
        )

        assert await cursor.fetchall() == []

    async def test_reconcile_creates_the_view_and_every_managed_function(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One DuckLake table gets its view, its helpers, and the routing functions."""
        await reconcile_one_table(postgres, monkeypatch, [("cpf", "BIGINT")])
        schema = postgres.namespace.schema

        assert [
            await relation_exists(postgres, schema, "people"),
            await function_exists(postgres, schema, "people_fn()"),
            await function_exists(
                postgres, schema, "people_dl_fn(text, text, boolean)"
            ),
            await function_exists(
                postgres, schema, "dl_changes_people(bigint, bigint)"
            ),
            await function_exists(postgres, schema, "dl_latest_snapshot()"),
            await function_exists(
                postgres,
                settings.DBOS_APP_SCHEMA,
                "plan_sources(text, text[], boolean)",
            ),
            await function_exists(postgres, schema, "people_bq_fn(text, text)"),
        ] == [True, True, True, True, True, True, False]

    async def test_reconcile_creates_the_bigquery_helper_of_a_fallback(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table that falls back to BigQuery gets a function that reads it."""
        await reconcile_one_table(
            postgres, monkeypatch, [("cpf", "BIGINT")], fallback=True
        )

        assert await function_exists(
            postgres, postgres.namespace.schema, "people_bq_fn(text, text)"
        )

    async def test_reconcile_replaces_the_columns_of_the_view_after_a_source_change(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A new source column reaches the view without dropping it."""
        await reconcile_one_table(
            postgres, monkeypatch, [("cpf", "BIGINT"), ("name", "VARCHAR")]
        )
        await reconcile_one_table(
            postgres,
            monkeypatch,
            [("cpf", "BIGINT"), ("name", "VARCHAR"), ("active", "BOOLEAN")],
        )

        rows = await fetch_all(
            postgres,
            "postgres/view_columns",
            params={"schema_name": postgres.namespace.schema, "table_name": "people"},
        )

        assert [row[0] for row in rows] == ["cpf", "name", "active"]

    async def test_reconcile_drops_the_views_and_functions_that_the_config_dropped(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table that leaves the config loses its view and its functions."""
        await reconcile_one_table(postgres, monkeypatch, [("cpf", "BIGINT")])
        schema = postgres.namespace.schema

        await reconcile_views(
            postgres.backend, SyncConfig(schemas={schema: SchemaConfig(tables=[])})
        )

        assert [
            await relation_exists(postgres, schema, "people"),
            await function_exists(postgres, schema, "people_fn()"),
            await function_exists(
                postgres, schema, "people_dl_fn(text, text, boolean)"
            ),
            await function_exists(
                postgres, schema, "dl_changes_people(bigint, bigint)"
            ),
        ] == [False, False, False, False]


@pytest.fixture
async def rbac(postgres: Postgres) -> Postgres:
    """Install the rbac schema from the real rules file in the test transaction."""
    await install_rbac(postgres)
    return postgres


REQUEST_ROLES: Final = ("dp_t_api", "dp_t_policy", "dp_t_admin")


async def create_request_roles(pg: Postgres, password: str) -> None:
    """Render create_roles for test role names and run it in the test transaction."""
    await pg.connection.execute(
        helm_sql(
            "create_roles",
            {
                "roles": SQL(", ").join(Literal(role) for role in REQUEST_ROLES),
                "baseline_role": Literal("dp_t_api"),
                "anonymous_role": Literal("anon_t"),
                "serving_role": Literal("auth_t"),
                "serving_password": Literal(password),
                "signer_role": Literal(SIGNER),
                "signer_password": Literal(password),
            },
        ).encode()
    )


@pytest.mark.postgres
class TestRolesTemplate:
    """The create_roles template: the roles that the requests use, and how they relate."""

    async def test_creates_the_request_roles_without_a_login(
        self, postgres: Postgres
    ) -> None:
        """The anonymous role and each role of the rules file exist and can't log in."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE ANY (ARRAY['dp\\_t\\_%', 'anon\\_t']) ORDER BY 1"
        )
        assert await cursor.fetchall() == [
            ("anon_t", False),
            ("dp_t_admin", False),
            ("dp_t_api", False),
            ("dp_t_policy", False),
        ]

    async def test_every_role_but_the_baseline_is_a_member_of_the_baseline(
        self, postgres: Postgres
    ) -> None:
        """The baseline role has every other role of the rules file as a member, and the serving login."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"""
            SELECT member.rolname FROM pg_auth_members AS m
            INNER JOIN pg_roles AS member ON member.oid = m.member
            INNER JOIN pg_roles AS parent ON parent.oid = m.roleid
            WHERE parent.rolname = 'dp_t_api' ORDER BY 1
            """
        )
        assert [row[0] for row in await cursor.fetchall()] == [
            "auth_t",
            "dp_t_admin",
            "dp_t_policy",
        ]

    async def test_the_serving_login_logs_in_and_does_not_inherit_the_request_roles(
        self, postgres: Postgres
    ) -> None:
        """The serving login has a password login, no inheritance, and every request role as a membership."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"SELECT rolcanlogin, rolinherit, rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname = 'auth_t'"
        )
        assert await cursor.fetchone() == (True, False, True)
        cursor = await postgres.connection.execute(
            b"""
            SELECT parent.rolname FROM pg_auth_members AS m
            INNER JOIN pg_roles AS parent ON parent.oid = m.roleid
            INNER JOIN pg_roles AS member ON member.oid = m.member
            WHERE member.rolname = 'auth_t' ORDER BY 1
            """
        )
        assert [row[0] for row in await cursor.fetchall()] == [
            "anon_t",
            "dp_t_admin",
            "dp_t_api",
            "dp_t_policy",
        ]

    async def test_the_signer_logs_in_and_belongs_to_no_request_role(
        self, postgres: Postgres
    ) -> None:
        """The signer has a password login and no inheritance, and no membership until a schema reader grants one."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            SQL(
                "SELECT rolcanlogin, rolinherit, rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname = {}"
            ).format(Literal(SIGNER))
        )
        assert await cursor.fetchone() == (True, False, True)
        cursor = await postgres.connection.execute(
            SQL(
                "SELECT count(*) FROM pg_auth_members AS m INNER JOIN pg_roles AS member ON member.oid = m.member WHERE member.rolname = {}"
            ).format(Literal(SIGNER))
        )
        assert await cursor.fetchone() == (0,)

    async def test_a_serving_login_that_exists_without_a_login_gets_one(
        self, postgres: Postgres
    ) -> None:
        """pg_duckdb creates the role of duckdb.postgres_role without a login, so the template sets the login."""
        await postgres.connection.execute(b"CREATE ROLE auth_t")
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"SELECT rolcanlogin, rolinherit FROM pg_roles WHERE rolname = 'auth_t'"
        )
        assert await cursor.fetchone() == (True, False)

    async def test_every_request_role_gets_the_statement_timeout_of_the_data_api(
        self, postgres: Postgres
    ) -> None:
        """Each request role carries its own statement timeout, because a membership doesn't pass settings on."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"""
            SELECT r.rolname, s FROM pg_db_role_setting AS d
            INNER JOIN pg_roles AS r ON r.oid = d.setrole
            CROSS JOIN LATERAL unnest(d.setconfig) AS s
            WHERE r.rolname LIKE ANY (ARRAY['dp\\_t\\_%', 'anon\\_t']) ORDER BY 1
            """
        )
        assert await cursor.fetchall() == [
            ("anon_t", "statement_timeout=15s"),
            ("dp_t_admin", "statement_timeout=15s"),
            ("dp_t_api", "statement_timeout=15s"),
            ("dp_t_policy", "statement_timeout=15s"),
        ]

    async def test_a_second_run_succeeds_and_sets_the_password_again(
        self, postgres: Postgres
    ) -> None:
        """A second run changes nothing but the password, and creates no role twice."""
        await create_request_roles(postgres, "first")
        first = await scalar(
            postgres, "SELECT rolpassword FROM pg_authid WHERE rolname = 'auth_t'"
        )
        await create_request_roles(postgres, "second")
        second = await scalar(
            postgres, "SELECT rolpassword FROM pg_authid WHERE rolname = 'auth_t'"
        )
        assert first != second
        cursor = await postgres.connection.execute(
            b"SELECT count(*) FROM pg_roles WHERE rolname LIKE ANY (ARRAY['dp\\_t\\_%', 'anon\\_t', 'auth\\_t'])"
        )
        assert await cursor.fetchone() == (len(REQUEST_ROLES) + 2,)


SIGNER: Final = "signer_t"


async def create_schema_reader(
    pg: Postgres, name: str, template: str = "create_schema_reader"
) -> None:
    """Render one reader template for a schema of the test catalog and run it in the test transaction."""
    await pg.connection.execute(
        helm_sql(
            template,
            {
                "name": Literal(name),
                "signer": Literal(SIGNER),
                "writer": Literal("test"),
            },
        ).encode()
    )


@pytest.mark.postgres
class TestSchemaReaderTemplates:
    """The reader template gives a role the metadata of one schema and nothing else."""

    async def prepare(self, pg: Postgres) -> None:
        """Create the signer login and the metadata schemas of two schemas, each with one table."""
        await pg.connection.execute(
            SQL("CREATE ROLE {} LOGIN NOINHERIT").format(Identifier(SIGNER))
        )
        for name in ("sales", "hr"):
            await pg.connection.execute(
                SQL(
                    "CREATE SCHEMA {schema}; CREATE TABLE {schema}.files (path text); INSERT INTO {schema}.files VALUES ({path})"
                ).format(schema=Identifier(name), path=Literal(f"{name}/a.parquet"))
            )
            await create_schema_reader(pg, name)

    async def read_as(
        self, pg: Postgres, role: str, query: str
    ) -> list[tuple[str]] | str:
        """Run a query as a role, and return the rows or the message of the refusal."""
        await pg.connection.execute(SQL("SET LOCAL ROLE {}").format(Identifier(SIGNER)))
        await pg.connection.execute(SQL("SET LOCAL ROLE {}").format(Identifier(role)))
        try:
            cursor = await pg.connection.execute(query.encode())
            return [(str(row[0]),) for row in await cursor.fetchall()]
        except psycopg.errors.InsufficientPrivilege as error:
            return str(error)

    async def test_the_role_reads_its_own_metadata(self, postgres: Postgres) -> None:
        """The signer switches into the role of a schema and reads that schema."""
        await self.prepare(postgres)
        assert await self.read_as(
            postgres, "schema_sales", "SELECT path FROM sales.files"
        ) == [("sales/a.parquet",)]

    async def test_the_role_cannot_read_the_metadata_of_another_schema(
        self, postgres: Postgres
    ) -> None:
        """The role of one schema gets a refusal on the schema of another."""
        await self.prepare(postgres)
        refusal = await self.read_as(
            postgres, "schema_sales", "SELECT path FROM hr.files"
        )

        assert "permission denied for schema hr" in str(refusal)

    async def test_the_role_cannot_write_the_metadata_of_its_own_schema(
        self, postgres: Postgres
    ) -> None:
        """The grant is a read grant."""
        await self.prepare(postgres)
        refusal = await self.read_as(
            postgres,
            "schema_sales",
            "INSERT INTO sales.files VALUES ('x') RETURNING path",
        )

        assert "permission denied for table files" in str(refusal)

    async def test_the_role_reads_a_table_that_the_catalog_creates_later(
        self, postgres: Postgres
    ) -> None:
        """DuckLake creates its tables after the role. A default privilege covers them."""
        await self.prepare(postgres)
        await postgres.connection.execute(SQL("CREATE TABLE sales.later (path text)"))
        assert await self.read_as(
            postgres, "schema_sales", "SELECT count(*)::text FROM sales.later"
        ) == [("0",)]

    async def test_a_second_run_succeeds(self, postgres: Postgres) -> None:
        """The template creates nothing twice."""
        await self.prepare(postgres)

        await create_schema_reader(postgres, "sales")

    async def test_drop_removes_the_role_and_keeps_the_schema(
        self, postgres: Postgres
    ) -> None:
        """The drop template removes the role and its grants. Another run does nothing."""
        await self.prepare(postgres)

        await create_schema_reader(postgres, "sales", "drop_schema_reader")
        await create_schema_reader(postgres, "sales", "drop_schema_reader")

        cursor = await postgres.connection.execute(
            b"SELECT rolname FROM pg_roles WHERE rolname LIKE 'schema\\_%'"
        )
        assert await cursor.fetchall() == [("schema_hr",)]

    async def test_signer_keys_are_the_only_table_that_the_signer_writes(
        self, postgres: Postgres
    ) -> None:
        """The signer stores and reads the sealed keys of its identities, and it can't delete them or read anything else."""
        await self.prepare(postgres)
        await postgres.connection.execute(
            helm_sql("create_signer_keys", {"signer": Literal(SIGNER)}).encode()
        )
        await postgres.connection.execute(
            SQL("SET LOCAL ROLE {}").format(Identifier(SIGNER))
        )

        await postgres.connection.execute(
            b"INSERT INTO signer.keys (schema_name, access_key, secret_sealed) VALUES ('sales', 'AKIA', 'sealed')"
        )
        cursor = await postgres.connection.execute(
            b"SELECT access_key, secret_sealed FROM signer.keys"
        )
        assert await cursor.fetchall() == [("AKIA", "sealed")]

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await postgres.connection.execute(b"DELETE FROM signer.keys")

    async def test_signer_keys_run_again_without_an_error(
        self, postgres: Postgres
    ) -> None:
        """The template creates nothing twice, and it keeps the rows."""
        await self.prepare(postgres)
        template = helm_sql("create_signer_keys", {"signer": Literal(SIGNER)}).encode()
        await postgres.connection.execute(template)
        await postgres.connection.execute(
            b"INSERT INTO signer.keys (schema_name, access_key, secret_sealed) VALUES ('sales', 'AKIA', 'sealed')"
        )

        await postgres.connection.execute(template)

        assert await scalar(postgres, "SELECT count(*) FROM signer.keys") == 1

    async def test_drop_forgets_the_keys_of_the_schema(
        self, postgres: Postgres
    ) -> None:
        """A removed schema leaves no sealed key behind, and the other schema keeps its own."""
        await self.prepare(postgres)
        await postgres.connection.execute(
            helm_sql("create_signer_keys", {"signer": Literal(SIGNER)}).encode()
        )
        await postgres.connection.execute(
            b"INSERT INTO signer.keys (schema_name, access_key, secret_sealed) VALUES ('sales', 'A', 's'), ('hr', 'B', 's')"
        )

        await create_schema_reader(postgres, "sales", "drop_schema_reader")

        cursor = await postgres.connection.execute(
            b"SELECT schema_name FROM signer.keys"
        )
        assert await cursor.fetchall() == [("hr",)]

    async def test_drop_works_before_the_keys_table_exists(
        self, postgres: Postgres
    ) -> None:
        """A cluster that never made an identity has no table, and the drop still works."""
        await self.prepare(postgres)

        await create_schema_reader(postgres, "sales", "drop_schema_reader")

    async def test_a_name_that_makes_the_role_too_long_fails(
        self, postgres: Postgres
    ) -> None:
        """A role name over 63 characters would collide with another after the truncation."""
        await postgres.connection.execute(
            SQL("CREATE ROLE {} LOGIN NOINHERIT").format(Identifier(SIGNER))
        )
        name = "a" * 60

        await postgres.connection.execute(
            SQL("CREATE SCHEMA {}").format(Identifier(name))
        )

        with pytest.raises(psycopg.errors.RaiseException, match="too long"):
            await create_schema_reader(postgres, name)


@pytest.mark.postgres
class TestRbacTemplate:
    """The create_rbac template: the rules in the file against the answers of rbac.can and rbac.mine."""

    @pytest.mark.parametrize(
        "role",
        [
            *(pytest.param(role.name, id=role.name) for role in RBAC.roles),
            pytest.param("dp_ghost", id="unknown-role"),
            pytest.param("", id="empty-role"),
        ],
    )
    async def test_can_answers_what_the_rules_give_to_the_role(
        self, rbac: Postgres, role: str
    ) -> None:
        """Every permission is allowed exactly when the role or a role that it inherits holds it."""
        await set_setting(rbac, "app.claim_role", role)
        await rbac.connection.execute(b"SELECT rbac.load()")

        answers = {
            feature: await scalar(rbac, "SELECT rbac.can(%s)", feature)
            for feature in RBAC.features
        }

        expected = RBAC.effective(role)
        assert answers == {feature: feature in expected for feature in RBAC.features}

    async def test_can_denies_every_permission_without_a_role_claim(
        self, rbac: Postgres
    ) -> None:
        """A request without a token has no role claim and no permission."""
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert [
            await scalar(rbac, "SELECT rbac.can(%s)", feature)
            for feature in RBAC.features
        ] == [False] * len(RBAC.features)

    @pytest.mark.parametrize(
        "feature", ["nope_nope", "", "read_api,read_sync", "read_api,", ",read_api"]
    )
    async def test_can_raises_for_a_permission_that_the_file_does_not_list(
        self, rbac: Postgres, feature: str
    ) -> None:
        """A name that no rule defines is a mistake of the code, so it fails loudly."""
        await set_setting(rbac, "app.claim_role", "dp_admin")
        await rbac.connection.execute(b"SELECT rbac.load()")

        with pytest.raises(psycopg.Error) as error:
            async with rbac.connection.transaction():
                await scalar(rbac, "SELECT rbac.can(%s)", feature)

        assert error.value.sqlstate == "PT500"

    @pytest.mark.parametrize(
        ("claim", "schema", "expected"),
        [
            pytest.param("s1,s2", "s1", True, id="first-of-two"),
            pytest.param("s1,s2", "s2", True, id="second-of-two"),
            pytest.param("s1", "s1", True, id="only-one"),
            pytest.param("s1,s2", "s", False, id="prefix-of-a-name"),
            pytest.param("s1,s2", "s10", False, id="longer-than-a-name"),
            pytest.param("s1,s2", "other", False, id="outside-the-claim"),
            pytest.param("s1,s2", "", False, id="empty-schema"),
            pytest.param("", "s1", False, id="empty-claim"),
            pytest.param("s1,s2", "s1,s2", False, id="comma-spans-two-names"),
            pytest.param("s1,s2", "s1,", False, id="trailing-comma"),
            pytest.param("s1,s2", ",s2", False, id="leading-comma"),
            pytest.param("s1,s2", ",", False, id="only-a-comma"),
        ],
    )
    async def test_mine_answers_whether_the_schema_is_in_the_claim(
        self, rbac: Postgres, claim: str, schema: str, expected: bool
    ) -> None:
        """A schema is mine only when the schemas claim names it exactly."""
        await set_setting(rbac, "app.claim_schemas", claim)
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert await scalar(rbac, "SELECT rbac.mine(%s)", schema) is expected

    async def test_mine_denies_every_schema_without_a_schemas_claim(
        self, rbac: Postgres
    ) -> None:
        """A request without the claim reaches no schema."""
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert await scalar(rbac, "SELECT rbac.mine(%s)", "s1") is False

    @pytest.mark.parametrize(
        "rules",
        [
            pytest.param(
                RBAC.changed("dp_console", inherits=["dp_ghost"]), id="unknown-parent"
            ),
            pytest.param(
                RBAC.changed("dp_api", permissions=["read_ghost"]),
                id="unknown-permission",
            ),
            pytest.param(
                RBAC.changed("dp_api", inherits=["dp_admin"]), id="inheritance-cycle"
            ),
            pytest.param(
                RBAC.changed("dp_api", inherits=["dp_api"]), id="role-inherits-itself"
            ),
            pytest.param(
                RBAC.model_copy(update={"roles": [*RBAC.roles, RBAC.roles[0]]}),
                id="repeated-role",
            ),
        ],
    )
    async def test_load_refuses_a_file_that_is_not_consistent_and_keeps_the_old_rules(
        self, rbac: Postgres, rules: RbacRules
    ) -> None:
        """One error rolls the whole load back, so the rules in the table stay as they were."""
        before = await stored_roles(rbac)

        with pytest.raises(psycopg.Error):
            async with rbac.connection.transaction():
                await install_rbac(rbac, rules)

        assert await stored_roles(rbac) == before

    async def test_load_refuses_a_role_that_does_not_exist_in_the_database(
        self, rbac: Postgres
    ) -> None:
        """The chart creates the roles of the file, so a role without a database role is a mismatch."""
        ghost = RbacRole(
            name="dp_missing", description="No database role", permissions=["read_api"]
        )

        with pytest.raises(psycopg.errors.UndefinedObject):
            async with rbac.connection.transaction():
                await install_rbac(
                    rbac, RBAC.model_copy(update={"roles": [*RBAC.roles, ghost]})
                )

    async def test_load_stores_the_closure_of_every_role_and_is_repeatable(
        self, rbac: Postgres
    ) -> None:
        """The table holds the inherited permissions, sorted, and a second load changes nothing."""
        first = await stored_roles(rbac)
        await install_rbac(rbac)

        assert await stored_roles(rbac) == first
        assert first == sorted(
            (role.name, sorted(RBAC.effective(role.name))) for role in RBAC.roles
        )

    async def test_load_removes_a_role_that_the_file_no_longer_has(
        self, rbac: Postgres
    ) -> None:
        """A role that left the file leaves the table at the next load."""
        extra = RbacRole(
            name="dp_extra", description="Test role", permissions=["read_api"]
        )
        await rbac.connection.execute(b"CREATE ROLE dp_extra NOLOGIN")
        await install_rbac(
            rbac, RBAC.model_copy(update={"roles": [*RBAC.roles, extra]})
        )
        assert "dp_extra" in [name for name, _ in await stored_roles(rbac)]

        await install_rbac(rbac)

        assert "dp_extra" not in [name for name, _ in await stored_roles(rbac)]

    async def test_load_records_the_hash_of_the_file_on_the_table(
        self, rbac: Postgres
    ) -> None:
        """The comment of the table says which version of the file it holds."""
        assert (
            await scalar(
                rbac, "SELECT obj_description('rbac.roles'::regclass, 'pg_class')"
            )
            == f"sha256:{SOURCE_HASH}"
        )

    async def test_load_raises_the_revision(self, rbac: Postgres) -> None:
        """Each load of the rules moves the revision, also when the file didn't change."""
        before = await revision(rbac)
        await install_rbac(rbac)

        assert await revision(rbac) > before

    async def test_load_makes_can_follow_the_new_rules(self, rbac: Postgres) -> None:
        """A second load with other rules changes the answer of rbac.can at once."""
        await set_setting(rbac, "app.claim_role", "dp_api")
        await rbac.connection.execute(b"SELECT rbac.load()")
        assert await scalar(rbac, "SELECT rbac.can('read_api')") is True

        await install_rbac(rbac, RBAC.changed("dp_api", permissions=[]))
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert await scalar(rbac, "SELECT rbac.can('read_api')") is False

    async def test_can_and_mine_are_inlined_into_the_query(
        self, rbac: Postgres
    ) -> None:
        """The planner replaces both calls with their expression, so a check costs no function call."""
        can = await plan_of(
            rbac, "SELECT 1 FROM generate_series(1, 3) AS g WHERE rbac.can('read_sync')"
        )
        mine = await plan_of(
            rbac,
            "SELECT 1 FROM generate_series(1, 3) AS g WHERE rbac.mine('s' || g)",
        )

        assert ("rbac.can(" in can, "strpos(" in can) == (False, True)
        assert ("rbac.mine(" in mine, "strpos(" in mine) == (False, True)


@pytest.mark.postgres
class TestVerifyRbacTemplate:
    """The verify_rbac template: the roles of the database against the roles of the file."""

    async def test_passes_when_the_database_matches_the_file(
        self, rbac: Postgres
    ) -> None:
        """The roles exist, the serving login is a member of each, and nothing else is."""
        await make_serving_login(rbac)

        await verify_rbac(rbac)

    @pytest.mark.parametrize(
        ("setup", "extra_roles", "message"),
        [
            pytest.param(
                None, ("dp_missing",), "dp_missing", id="role-without-a-database-role"
            ),
            pytest.param(
                "REVOKE dp_admin FROM auth_v",
                (),
                "auth_v is not a member of dp_admin",
                id="serving-login-misses-a-role",
            ),
            pytest.param(
                "CREATE ROLE dp_stale NOLOGIN; GRANT dp_stale TO auth_v",
                (),
                "dp_stale",
                id="serving-login-has-a-role-that-left-the-file",
            ),
            pytest.param(
                "ALTER ROLE dp_api LOGIN", (), "dp_api can log in", id="role-can-log-in"
            ),
            pytest.param(
                "ALTER ROLE dp_api SUPERUSER",
                (),
                "dp_api is a superuser",
                id="role-is-a-superuser",
            ),
            pytest.param(
                "ALTER ROLE auth_v INHERIT",
                (),
                "auth_v inherits",
                id="serving-login-inherits",
            ),
            pytest.param(
                "DELETE FROM rbac.roles WHERE name = 'dp_admin'",
                (),
                "dp_admin is not in rbac.roles",
                id="rules-not-loaded",
            ),
        ],
    )
    async def test_fails_and_names_each_difference(
        self,
        rbac: Postgres,
        setup: str | None,
        extra_roles: tuple[str, ...],
        message: str,
    ) -> None:
        """A difference stops the job, and the error names it, so that a stale role never keeps a right."""
        await make_serving_login(rbac)
        if setup:
            await rbac.connection.execute(setup.encode())

        with pytest.raises(psycopg.errors.RaiseException, match=re.escape(message)):
            async with rbac.connection.transaction():
                await verify_rbac(rbac, extra_roles)


EXAMPLES: Final = int(environ.get("RBAC_EXAMPLES", "300"))

ROLE_POOL: Final = [f"dp_p{index}" for index in range(8)]

WORD: Final = st.from_regex(r"[a-z]{1,5}", fullmatch=True)

DESCRIPTION: Final = st.one_of(
    st.text(
        alphabet=st.characters(
            blacklist_categories=("Cs",), blacklist_characters="\x00"
        ),
        max_size=24,
    ),
    st.lists(
        st.sampled_from(
            ["$rbac$", "$$", "'", '"', "\\", "{{", "}}", "{%", "\n", "\u00e9", " "]
        ),
        max_size=6,
    ).map("".join),
)

CLAIM_PART: Final = st.text(alphabet="abc0_\u00e9 ,", max_size=3)

PROPERTIES: Final = hypothesis_settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@st.composite
def rbac_rules(draw: st.DrawFn, max_roles: int = 8) -> RbacRules:
    """Draw a consistent rules file: random permissions, and roles that inherit only earlier roles."""
    subjects = draw(
        st.dictionaries(
            WORD,
            st.sets(
                WORD.filter(lambda action: action != "description"),
                min_size=1,
                max_size=3,
            ),
            min_size=1,
            max_size=4,
        )
    )
    permissions = {
        subject: {
            "description": draw(DESCRIPTION),
            **{action: draw(DESCRIPTION) for action in sorted(actions)},
        }
        for subject, actions in subjects.items()
    }
    features = sorted(
        f"{action}_{subject}"
        for subject, actions in subjects.items()
        for action in actions
    )
    names = ROLE_POOL[: draw(st.integers(min_value=1, max_value=max_roles))]
    roles = [
        RbacRole(
            name=name,
            description=draw(DESCRIPTION),
            inherits=(
                draw(st.lists(st.sampled_from(names[:index]), unique=True, max_size=3))
                if index
                else []
            ),
            permissions=draw(st.lists(st.sampled_from(features), unique=True)),
        )
        for index, name in enumerate(names)
    ]

    return RbacRules(permissions=permissions, roles=draw(st.permutations(roles)))


@st.composite
def claim_and_schema(draw: st.DrawFn) -> tuple[str, str]:
    """Draw a schemas claim and a schema name, often one element of the claim."""
    parts = draw(st.lists(CLAIM_PART, max_size=4))
    schema = draw(
        st.one_of(CLAIM_PART, st.sampled_from(parts) if parts else CLAIM_PART)
    )

    return ",".join(parts), schema


@pytest.fixture
def rbac_connection() -> Iterator[psycopg.Connection[TupleRow]]:
    """Give a property test one synchronous connection to the test database."""
    with psycopg.connect(f"{POSTGRES_URL}/test_template") as connection:
        yield connection
        connection.rollback()


def install_rbac_sync(
    connection: psycopg.Connection[TupleRow], rules: RbacRules
) -> None:
    """Install a rules file in the open transaction of a property test."""
    connection.execute(
        helm_sql(
            "create_rbac",
            {
                "rules": rules.context(),
                "anonymous_role": Identifier("anon"),
                "source_hash": SOURCE_HASH,
            },
        ).encode()
    )


def stored_roles_sync(
    connection: psycopg.Connection[TupleRow],
) -> list[tuple[str, list[str]]]:
    """Return the rows of rbac.roles."""
    rows = connection.execute(
        b"SELECT name, permissions FROM rbac.roles ORDER BY name"
    ).fetchall()

    return [(str(name), list(permissions)) for name, permissions in rows]


def answers_for(
    connection: psycopg.Connection[TupleRow], role: str, features: list[str]
) -> dict[str, bool]:
    """Ask rbac.can about every feature as a token with one role."""
    connection.execute("SELECT set_config('app.claim_role', %s, true)", (role,))
    connection.execute(b"SELECT rbac.load()")
    rows = connection.execute(
        "SELECT feature, rbac.can(feature) FROM unnest(%s::text[]) AS feature",
        (features,),
    ).fetchall()

    return {str(feature): bool(answer) for feature, answer in rows}


FAULTS: Final[dict[str, Callable[[RbacRules], RbacRules]]] = {
    "unknown-parent": lambda rules: rules.changed(
        rules.roles[0].name, inherits=[*rules.roles[0].inherits, "dp_ghost"]
    ),
    "unknown-permission": lambda rules: rules.changed(
        rules.roles[0].name,
        permissions=[*rules.roles[0].permissions, "zzzzzz_zzzzzz"],
    ),
    "role-inherits-itself": lambda rules: rules.changed(
        rules.roles[0].name, inherits=[*rules.roles[0].inherits, rules.roles[0].name]
    ),
    "repeated-role": lambda rules: rules.model_copy(
        update={"roles": [*rules.roles, rules.roles[0]]}
    ),
    "two-roles-in-a-cycle": lambda rules: rules.model_copy(
        update={
            "roles": [
                *(
                    role
                    for role in rules.roles
                    if role.name not in {ROLE_POOL[6], ROLE_POOL[7]}
                ),
                RbacRole(
                    name=ROLE_POOL[6],
                    description="a",
                    inherits=[ROLE_POOL[7]],
                    permissions=[],
                ),
                RbacRole(
                    name=ROLE_POOL[7],
                    description="b",
                    inherits=[ROLE_POOL[6]],
                    permissions=[],
                ),
            ]
        }
    ),
}


@pytest.mark.postgres
class TestRbacProperties:
    """Random rules files and random names against a reference that the test computes in Python."""

    @PROPERTIES
    @given(rules=rbac_rules())
    def test_can_answers_the_closure_of_the_role_for_any_rules(
        self, rbac_connection: psycopg.Connection[TupleRow], rules: RbacRules
    ) -> None:
        """Every role of every random file is allowed exactly what it holds or inherits."""
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, rules)

            for role in [*(role.name for role in rules.roles), "dp_ghost", ""]:
                expected = rules.effective(role)
                assert answers_for(rbac_connection, role, rules.features) == {
                    feature: feature in expected for feature in rules.features
                }

    @PROPERTIES
    @given(rules=rbac_rules(), seed=st.randoms(use_true_random=False))
    def test_the_stored_rules_do_not_depend_on_the_order_of_the_file(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        rules: RbacRules,
        seed: Random,
    ) -> None:
        """Shuffling the roles and their lists gives the same table."""
        shuffled = rules.model_copy(
            update={
                "roles": [
                    role.model_copy(
                        update={
                            "inherits": seed.sample(role.inherits, len(role.inherits)),
                            "permissions": seed.sample(
                                role.permissions, len(role.permissions)
                            ),
                        }
                    )
                    for role in seed.sample(rules.roles, len(rules.roles))
                ]
            }
        )
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, rules)
            first = stored_roles_sync(rbac_connection)
            install_rbac_sync(rbac_connection, shuffled)

            assert stored_roles_sync(rbac_connection) == first

    @PROPERTIES
    @given(first=rbac_rules(), second=rbac_rules())
    def test_a_second_file_replaces_the_first_completely(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        first: RbacRules,
        second: RbacRules,
    ) -> None:
        """No role, and no permission, of the earlier file survives in the table."""
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, first)
            install_rbac_sync(rbac_connection, second)

            assert stored_roles_sync(rbac_connection) == sorted(
                (role.name, sorted(second.effective(role.name)))
                for role in second.roles
            )

    @PROPERTIES
    @given(rules=rbac_rules(max_roles=6), fault=st.sampled_from(sorted(FAULTS)))
    def test_a_file_with_one_fault_is_refused_and_leaves_the_rules_unchanged(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        rules: RbacRules,
        fault: str,
    ) -> None:
        """Whatever the valid file around it, one fault fails the load and changes nothing."""
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, rules)
            before = stored_roles_sync(rbac_connection)

            with pytest.raises(psycopg.Error), rbac_connection.transaction():
                install_rbac_sync(rbac_connection, FAULTS[fault](rules))

            assert stored_roles_sync(rbac_connection) == before

    @PROPERTIES
    @given(rules=rbac_rules(max_roles=8), data=st.data())
    def test_a_new_inherits_edge_never_removes_a_permission(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        rules: RbacRules,
        data: st.DataObject,
    ) -> None:
        """Letting a role inherit one more earlier role can only add permissions."""
        ordered = sorted(rules.roles, key=lambda role: role.name)
        if len(ordered) < 2:
            return
        child = data.draw(st.integers(min_value=1, max_value=len(ordered) - 1))
        parent = data.draw(st.integers(min_value=0, max_value=child - 1))
        more = rules.changed(
            ordered[child].name,
            inherits=[*ordered[child].inherits, ordered[parent].name],
        )
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, rules)
            before = dict(stored_roles_sync(rbac_connection))
            install_rbac_sync(rbac_connection, more)
            after = dict(stored_roles_sync(rbac_connection))

        assert all(set(before[name]) <= set(after[name]) for name in before)

    @PROPERTIES
    @given(case=claim_and_schema())
    def test_mine_is_true_only_for_one_whole_element_of_the_claim(
        self, rbac_connection: psycopg.Connection[TupleRow], case: tuple[str, str]
    ) -> None:
        """Any text for the claim and for the schema: only a non-empty exact element matches."""
        claim, schema = case
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, RBAC)
            rbac_connection.execute(
                "SELECT set_config('app.claim_schemas', %s, true)", (claim,)
            )
            rbac_connection.execute(b"SELECT rbac.load()")

            answer = rbac_connection.execute(
                "SELECT rbac.mine(%s)", (schema,)
            ).fetchone()

        assert answer == (
            bool(schema) and "," not in schema and schema in claim.split(","),
        )

    @PROPERTIES
    @given(
        role=st.sampled_from([*(role.name for role in RBAC.roles), "dp_ghost"]),
        feature=st.one_of(
            st.sampled_from(RBAC.features),
            st.sampled_from(RBAC.features).map(operator.itemgetter(slice(-1))),
            st.lists(st.sampled_from(RBAC.features), min_size=2, max_size=3).map(
                ",".join
            ),
            st.text(
                alphabet=st.characters(
                    blacklist_categories=("Cs",), blacklist_characters="\x00"
                ),
                max_size=12,
            ),
            st.just(""),
            st.none(),
        ),
    )
    def test_can_allows_only_a_held_permission_and_raises_for_any_other_name(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        role: str,
        feature: str | None,
    ) -> None:
        """A name that the file lists gives true or false by the role. Any other text, a comma list, empty or null raises."""
        listed = feature in RBAC.features
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, RBAC)
            rbac_connection.execute(
                "SELECT set_config('app.claim_role', %s, true)", (role,)
            )
            rbac_connection.execute(b"SELECT rbac.load()")

            if listed:
                answer = rbac_connection.execute(
                    "SELECT rbac.can(%s)", (feature,)
                ).fetchone()
                assert answer == (feature in RBAC.effective(role),)
            else:
                with (
                    pytest.raises(psycopg.Error) as error,
                    rbac_connection.transaction(),
                ):
                    rbac_connection.execute("SELECT rbac.can(%s)", (feature,))
                assert error.value.sqlstate == "PT500"
