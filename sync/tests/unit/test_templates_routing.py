"""Tests for the routing templates and the function of a table."""

import json
import re
from typing import Final

import psycopg
import pytest
from psycopg.sql import SQL, Identifier

from sync.conditions import selection_condition
from sync.models import (
    FullTable,
    PublicationResult,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncPlan,
    TableState,
)
from sync.settings import settings
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
from tests.constants import (
    ROUTED_TABLE,
)
from tests.fixtures.types import Postgres
from tests.helpers.core import fetch_all, scalar, set_request_headers, set_setting
from tests.helpers.routing import (
    act_as_token,
    call_table_function,
    grant_unit,
    insert_access_policy,
    install_table_function,
)
from tests.helpers.state import partitioned, put_state

FULL: Final = TableState(strategy=Strategy.FULL, signature="s")


TWO_DAYS: Final = (
    "(\"date\" >= '2026-09-23' AND \"date\" < '2026-09-24')"
    " OR (\"date\" >= '2026-09-25' AND \"date\" < '2026-09-26')"
)


RLS_WHERE: Final = "WHERE (unit_id IN ('u1'))"


@pytest.mark.postgres
class TestRoutingCoverage:
    """The routing functions that read the snapshot and the coverage of a table."""

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


@pytest.mark.postgres
class TestRoutingPlan:
    """The routing functions that plan the sources of a request and set its response headers."""

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
        await routing.connection.execute(
            "SELECT data_proxy.set_response_headers(%s, %s)", (label, snapshot)
        )

        raw = await scalar(routing, "SELECT current_setting('response.headers', true)")
        assert json.loads(str(raw)) == expected


@pytest.mark.postgres
class TestTableFunction:
    """The function of a table, with the checks of the token and the access policy."""

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

    async def test_view_function_passes_the_grants_as_a_list_without_a_fallback(
        self, table_function: Postgres
    ) -> None:
        """DuckDB alone reads the predicate of a table without a fallback, so it gets the list form."""
        await install_table_function(table_function, has_rls=True, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)
        await grant_unit(table_function, "u")
        await insert_access_policy(table_function, "u", "unit", "u2")

        rows, _ = await call_table_function(table_function)

        assert len(rows) == 1
        source, where_value, snapshot = rows[0]
        where = str(where_value)
        assert (source, snapshot) == ("ducklake", "7")
        assert (
            re.fullmatch(
                r"WHERE \(CAST\(unit_id AS VARCHAR\) IN \(SELECT unnest\(\['u[12]','u[12]'\]\)\)\)",
                where,
            )
            is not None
        )
        assert {"'u1'", "'u2'"} <= set(
            where.split("[")[1].split("]", maxsplit=1)[0].split(",")
        )

    async def test_view_function_passes_an_empty_predicate_to_the_wildcard_schema_claim(
        self, table_function: Postgres
    ) -> None:
        """A token for every schema reads every row, with no grant in the access policy."""
        await install_table_function(table_function, has_rls=True, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)

        rows, _ = await call_table_function(table_function, schemas="*")

        assert rows == [("ducklake", "", "7")]

    async def test_view_function_passes_an_empty_predicate_to_the_schema_wildcard_claim(
        self, table_function: Postgres
    ) -> None:
        """A token with schema:* reads every row of that schema, with no grant in the access policy."""
        await install_table_function(table_function, has_rls=True, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)

        rows, _ = await call_table_function(
            table_function, schemas=f"{table_function.namespace.schema}:*"
        )

        assert rows == [("ducklake", "", "7")]

    async def test_view_function_returns_no_row_for_the_schema_wildcard_of_another_schema(
        self, table_function: Postgres
    ) -> None:
        """A token with other:* reaches neither the rows nor the schema of this table."""
        await install_table_function(table_function, has_rls=True, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)
        await grant_unit(table_function, "u")

        rows, _ = await call_table_function(table_function, schemas="other:*")

        assert rows == []

    async def test_view_function_passes_an_empty_predicate_to_a_subject_with_the_wildcard_grant(
        self, table_function: Postgres
    ) -> None:
        """A subject with the grant (*, *) reads every row of the schema, with no unit grant."""
        await install_table_function(table_function, has_rls=True, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)
        await set_setting(table_function, "app.claim_sub", "u")
        await insert_access_policy(table_function, "u", "*", "*")

        rows, _ = await call_table_function(table_function)

        assert rows == [("ducklake", "", "7")]

    @pytest.mark.parametrize(
        ("subject", "unit_type", "unit_id"),
        [
            pytest.param("other", "*", "*", id="grant-of-another-subject"),
            pytest.param("u", "*", "u1", id="wildcard-type-only"),
        ],
    )
    async def test_view_function_returns_no_row_for_a_partial_wildcard_grant(
        self, table_function: Postgres, subject: str, unit_type: str, unit_id: str
    ) -> None:
        """Only the grant (*, *) of the subject itself lifts the row filter."""
        await install_table_function(table_function, has_rls=True, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)
        await set_setting(table_function, "app.claim_sub", "u")
        await insert_access_policy(table_function, subject, unit_type, unit_id)

        rows, _ = await call_table_function(table_function)

        assert rows == []

    async def test_view_function_returns_no_row_without_a_grant_or_the_wildcard(
        self, table_function: Postgres
    ) -> None:
        """A token for one schema still needs a grant for the rows of a table with row rules."""
        await install_table_function(table_function, has_rls=True, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)

        rows, _ = await call_table_function(table_function)

        assert rows == []

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
