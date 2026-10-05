"""Integration tests for the SQL routing functions and the per-table function."""

import json
from typing import Final

import psycopg
import pytest
from psycopg.rows import TupleRow
from psycopg.sql import Identifier

from data_proxy.conditions import selection_condition
from data_proxy.executor import Executor
from data_proxy.models import (
    FullTable,
    PublicationResult,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncPlan,
    TableState,
)
from data_proxy.settings import settings
from data_proxy.sources.partitions import (
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from data_proxy.state import build_table_states, write_table_states
from data_proxy.types import PostgresParams
from data_proxy.views.mappings import function_columns
from tests.constants import ROUTED_TABLE
from tests.fixtures.types import Postgres
from tests.helpers import (
    call_table_function,
    can_execute,
    fetch_all,
    grant_unit,
    install_table_function,
    partitioned,
    put_state,
    scalar,
    set_request_headers,
    set_setting,
)

pytestmark = pytest.mark.postgres

FULL: Final = TableState(strategy=Strategy.FULL, signature="s")
TWO_DAYS: Final = (
    "(\"date\" >= '2026-09-23' AND \"date\" < '2026-09-24')"
    " OR (\"date\" >= '2026-09-25' AND \"date\" < '2026-09-26')"
)
RLS_WHERE: Final = "WHERE (unit_id IN ('u1'))"


class TestRequestedSnapshot:
    """requested_snapshot behavior tests."""

    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            pytest.param(None, None, id="no-header-setting"),
            pytest.param({}, None, id="no-headers"),
            pytest.param({"x-ducklake-snapshot": "42"}, 42, id="numeric"),
            pytest.param({"X-DuckLake-Snapshot": "42"}, 42, id="mixed-case-name"),
        ],
    )
    async def test_reads_the_pinned_version(
        self, routing: Postgres, headers: dict[str, str] | None, expected: int | None
    ) -> None:
        if headers is not None:
            await set_request_headers(routing, headers)

        assert (
            await scalar(routing, "SELECT data_proxy.requested_snapshot()") == expected
        )

    @pytest.mark.parametrize("value", ["abc", "-1", "1.5", "", "1" * 19])
    async def test_rejects_a_version_that_is_not_a_whole_number(
        self, routing: Postgres, value: str
    ) -> None:
        await set_request_headers(routing, {"x-ducklake-snapshot": value})

        with pytest.raises(psycopg.Error) as error:
            await routing.connection.execute("SELECT data_proxy.requested_snapshot()")

        assert error.value.sqlstate == "PT400"


class TestSelectionCondition:
    """selection_condition matches the Python predicate builder."""

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
    async def test_renders_the_same_predicate_as_python(
        self,
        routing: Postgres,
        selection: TimeRangeSelection | RangeSelection | RemainderSelection,
    ) -> None:
        rendered = await scalar(
            routing,
            "SELECT data_proxy.selection_condition(%s::jsonb)",
            selection.model_dump_json(),
        )

        assert rendered == selection_condition(selection).as_string(None)


class TestCoveredBy:
    """covered_by_ducklake and covered_by_fallback behavior tests."""

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
    async def test_splits_the_table_between_the_sources(
        self,
        routing: Postgres,
        state: TableState | None,
        ducklake: str | None,
        fallback: str | None,
    ) -> None:
        await put_state(routing, ROUTED_TABLE, state)

        covered_by_ducklake = await scalar(
            routing, "SELECT data_proxy.covered_by_ducklake(%s)", ROUTED_TABLE
        )
        covered_by_fallback = await scalar(
            routing, "SELECT data_proxy.covered_by_fallback(%s)", ROUTED_TABLE
        )

        assert covered_by_ducklake == ducklake
        assert covered_by_fallback == fallback

    async def test_reads_state_built_for_a_published_table(
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
                "SELECT data_proxy.covered_by_ducklake(%s)",
                ROUTED_TABLE,
            )
            == "TRUE"
        )


class TestPlanSources:
    """plan_sources rule tests: one row per source."""

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
    async def test_applies_the_first_matching_rule(
        self,
        routing: Postgres,
        state: TableState | None,
        fallbacks: list[str],
        pinned: bool,
        expected: list[tuple[str, bool, str | None]],
    ) -> None:
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

    async def test_rejects_an_unpublished_table_that_has_no_fallback(
        self, routing: Postgres
    ) -> None:
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


class TestSourceLabel:
    """source_label behavior tests."""

    @pytest.mark.parametrize(
        ("sources", "expected"),
        [
            pytest.param(["ducklake"], "ducklake", id="ducklake"),
            pytest.param(["bigquery"], "bigquery", id="bigquery"),
            pytest.param(["ducklake", "bigquery"], "ducklake+bigquery", id="both"),
            pytest.param([], None, id="none"),
        ],
    )
    async def test_names_the_sources_queried(
        self,
        routing: Postgres,
        sources: list[str],
        expected: str | None,
    ) -> None:
        label = await scalar(
            routing,
            "SELECT data_proxy.source_label(%s)",
            sources,
        )

        assert label == expected


class TestAndWhere:
    """and_where behavior tests."""

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
    async def test_adds_a_condition_to_the_where_clause(
        self, routing: Postgres, where: str, condition: str | None, expected: str
    ) -> None:
        combined = await scalar(
            routing, "SELECT data_proxy.and_where(%s, %s)", where, condition
        )

        assert combined == expected


class TestSetResponseHeaders:
    """set_response_headers behavior tests."""

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
    async def test_sets_the_postgrest_response_headers(
        self,
        routing: Postgres,
        label: str | None,
        snapshot: int | None,
        expected: list[dict[str, str]],
    ) -> None:
        await routing.connection.execute(
            "SELECT data_proxy.set_response_headers(%s, %s)", (label, snapshot)
        )

        raw = await scalar(routing, "SELECT current_setting('response.headers', true)")
        assert json.loads(str(raw)) == expected


class TestTableFunction:
    """Per-table function behavior tests."""

    async def test_stops_a_denied_user_before_any_source_and_sets_no_headers(
        self, table_function: Postgres
    ) -> None:
        await install_table_function(
            table_function, has_rls=True, fallbacks=["bigquery"]
        )
        await put_state(table_function, ROUTED_TABLE, partitioned(23, 25))
        await set_setting(table_function, "app.claim_sub", "nobody")

        rows, headers = await call_table_function(table_function)

        assert rows == []
        assert headers == {}

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
    async def test_calls_only_the_planned_helpers(
        self,
        table_function: Postgres,
        state: TableState | None,
        fallbacks: list[str],
        pinned: str | None,
        sources: list[str],
        label: str,
        snapshot: str | None,
    ) -> None:
        await install_table_function(table_function, has_rls=True, fallbacks=fallbacks)
        await put_state(table_function, ROUTED_TABLE, state)
        await grant_unit(table_function, "u")
        if pinned is not None:
            await set_request_headers(table_function, {"x-ducklake-snapshot": pinned})

        rows, headers = await call_table_function(table_function)

        assert [row[0] for row in rows] == sources
        assert headers.get("X-Source") == label
        assert headers.get("X-DuckLake-Snapshot") == snapshot

    async def test_passes_the_rls_predicate_and_the_bigquery_coverage(
        self, table_function: Postgres
    ) -> None:
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

    async def test_passes_an_empty_predicate_without_rls(
        self, table_function: Postgres
    ) -> None:
        await install_table_function(table_function, has_rls=False, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)

        rows, _ = await call_table_function(table_function)

        assert rows == [("ducklake", "", "7")]

    async def test_an_empty_ducklake_result_never_calls_bigquery(
        self, table_function: Postgres
    ) -> None:
        await install_table_function(
            table_function, has_rls=True, fallbacks=["bigquery"]
        )
        await put_state(table_function, ROUTED_TABLE, FULL)
        await grant_unit(table_function, "u")
        await set_setting(table_function, "test.dl_empty", "on")

        rows, headers = await call_table_function(table_function)

        assert rows == []
        assert headers["X-Source"] == "ducklake"


class TestFunctionPrivileges:
    """Only the table function is executable by the role that reads the view."""

    async def test_the_user_role_runs_the_table_function_but_not_the_helpers(
        self, table_function: Postgres
    ) -> None:
        schema = table_function.namespace.schema
        columns = function_columns([("source", "VARCHAR")], raw_json=True)
        common = {
            "schema": Identifier(schema),
            "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
            "columns": columns,
        }
        executor = Executor[PostgresParams, list[TupleRow]](conn=table_function.backend)
        await install_table_function(
            table_function, has_rls=False, fallbacks=["bigquery"]
        )
        await executor.execute(
            "postgres/views/ducklake",
            mapping={
                **common,
                "function": Identifier("p_dl_fn"),
                "duckdb_view": "p_view",
                "source": "dl.p",
                "catalog_local_path": "/var/lib/ducklake/catalogs/p/catalog.sqlite",
                "data_path": "s3://bucket/p",
            },
        )
        await executor.execute(
            "postgres/views/fallbacks/bigquery",
            mapping={
                **common,
                "function": Identifier("p_bq_fn"),
                "duckdb_view": "p_view",
                "load": "LOAD bigquery",
                "source": "bigquery_scan(''p.d.p'')",
            },
        )

        assert await can_execute(table_function, "t_fn()")
        assert not await can_execute(table_function, "p_dl_fn(text, text)")
        assert not await can_execute(table_function, "p_bq_fn(text, text)")
