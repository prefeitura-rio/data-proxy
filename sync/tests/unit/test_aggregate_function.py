"""Tests for the function that runs the aggregate of a request inside DuckDB."""

import json
from decimal import Decimal

import psycopg
import pytest
from pydantic import TypeAdapter

from sync.models import TableState
from tests.constants import FULL, ROUTED_TABLE
from tests.fixtures.types import Postgres
from tests.helpers.core import scalar, set_setting
from tests.helpers.routing import (
    aggregate,
    by,
    call_aggregate_function,
    fetch_all_rows,
    grant_everything,
    grant_unit,
    insert_access_policy,
    install_aggregate_function,
    ordered,
    response_status,
    total,
)
from tests.helpers.state import partitioned, put_state


@pytest.mark.postgres
class TestAggregateResults:
    """The function gives the rows that a group by of the table function would give."""

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param(
                aggregate(total(), group=["grp"]),
                [{"grp": "a", "n": 11}, {"grp": "b", "n": 36}],
                id="sum-by-group-ignores-null",
            ),
            pytest.param(
                aggregate({"fn": "count", "alias": "total"}, group=["grp"]),
                [{"grp": "a", "total": 3}, {"grp": "b", "total": 3}],
                id="count-of-rows",
            ),
            pytest.param(
                aggregate(
                    {"fn": "count", "column": "qty", "alias": "total"}, group=["grp"]
                ),
                [{"grp": "a", "total": 3}, {"grp": "b", "total": 2}],
                id="count-of-a-column-skips-null",
            ),
            pytest.param(
                aggregate(
                    {"fn": "min", "column": "qty", "alias": "low"},
                    {"fn": "max", "column": "qty", "alias": "high"},
                ),
                [{"low": 1, "high": 32}],
                id="min-and-max-without-a-group",
            ),
            pytest.param(
                aggregate(
                    {"fn": "max", "column": "day", "alias": "last"}, group=["grp"]
                ),
                [
                    {"grp": "a", "last": "2026-04-01"},
                    {"grp": "b", "last": "2026-06-01"},
                ],
                id="max-of-a-date",
            ),
            pytest.param(
                aggregate(total(), group=["grp", "flag"]),
                [
                    {"grp": "a", "flag": False, "n": 2},
                    {"grp": "a", "flag": True, "n": 9},
                    {"grp": "b", "flag": False, "n": None},
                    {"grp": "b", "flag": True, "n": 36},
                ],
                id="group-by-two-columns",
            ),
            pytest.param(aggregate(total()), [{"n": 47}], id="sum-without-a-group"),
            pytest.param(
                aggregate({"fn": "sum", "column": "amount", "alias": "n"}),
                [{"n": 17.6}],
                id="sum-of-a-decimal-column",
            ),
            pytest.param(
                aggregate(
                    {"fn": "min", "column": "ratio", "alias": "low"},
                    {"fn": "max", "column": "ratio", "alias": "high"},
                ),
                [{"low": 0.5, "high": 6.0}],
                id="min-and-max-of-a-decimal-column",
            ),
            pytest.param(
                aggregate(total("numerador"), {"fn": "count", "alias": "denominador"}),
                [{"numerador": 47, "denominador": 6}],
                id="two-aliases",
            ),
        ],
    )
    async def test_gives_the_rows_of_the_group_by(
        self,
        installed: Postgres,
        document: dict[str, object],
        expected: list[dict[str, object]],
    ) -> None:
        """Gives one row for each group, with the aliases that the request names."""
        answer, _ = await call_aggregate_function(installed, document)

        assert ordered(answer) == pytest.approx(ordered(expected))

    async def test_applies_the_filter_before_the_aggregate(
        self, installed: Postgres
    ) -> None:
        """Sums only the rows that the where of the request keeps."""
        document = aggregate(total(), group=["grp"]) | {
            "where": {
                "kind": "comparison",
                "column": "unit_id",
                "operator": "eq",
                "negated": False,
                "value": "u1",
            }
        }

        answer, _ = await call_aggregate_function(installed, document)

        assert ordered(answer) == [{"grp": "a", "n": 3}, {"grp": "b", "n": 4}]

    async def test_gives_an_empty_list_for_a_group_by_with_no_row(
        self, installed: Postgres
    ) -> None:
        """Gives a list with no item when the filter keeps no row, as the table does."""
        document = aggregate(total(), group=["grp"]) | {
            "where": {
                "kind": "comparison",
                "column": "unit_id",
                "operator": "eq",
                "negated": False,
                "value": "nobody",
            }
        }

        answer, _ = await call_aggregate_function(installed, document)

        assert answer == []

    async def test_sets_the_source_and_the_snapshot_headers(
        self, installed: Postgres
    ) -> None:
        """Sets the same response headers as the table function."""
        _, headers = await call_aggregate_function(installed, aggregate(total()))

        assert headers == {"X-Source": "ducklake", "X-DuckLake-Snapshot": "7"}

    async def test_quotes_an_alias_that_holds_a_quote(
        self, installed: Postgres
    ) -> None:
        """An alias is a name in the answer, and it never changes the query."""
        alias = 'n"; DROP VIEW dl_t; --'

        answer, _ = await call_aggregate_function(installed, aggregate(total(alias)))

        assert answer == [{alias: 47}]
        again, _ = await call_aggregate_function(installed, aggregate(total()))
        assert again == [{"n": 47}]


@pytest.mark.postgres
class TestAggregateGroupAndTail:
    """The function names the group columns and orders and cuts the rows of the groups."""

    async def test_names_a_group_column_with_its_alias(
        self, installed: Postgres
    ) -> None:
        """Gives the alias of a group column as the key of the value."""
        answer, _ = await call_aggregate_function(
            installed, aggregate(total(), group=[("grp", "grupo")])
        )

        assert ordered(answer) == [{"grupo": "a", "n": 11}, {"grupo": "b", "n": 36}]

    async def test_groups_by_the_columns_when_an_alias_repeats_another_column(
        self, installed: Postgres
    ) -> None:
        """Groups by the column and not by the name that another alias gives it."""
        document = aggregate(total(), group=[("grp", "unit_id"), ("unit_id", "grp")])

        answer, _ = await call_aggregate_function(installed, document)

        assert ordered(answer) == ordered(
            [
                {"unit_id": "a", "grp": "u1", "n": 3},
                {"unit_id": "a", "grp": "u2", "n": 8},
                {"unit_id": "b", "grp": "u1", "n": 4},
                {"unit_id": "b", "grp": "u2", "n": None},
                {"unit_id": "b", "grp": "u3", "n": 32},
            ]
        )

    @pytest.mark.parametrize("descending", [False, True])
    async def test_orders_a_text_column_with_the_collation_of_postgresql(
        self, installed: Postgres, descending: bool
    ) -> None:
        """Gives the order that ORDER BY gives, which is not the order of the bytes."""
        labels = ["Zeta", "alpha", "Beta", "zeta", "Alpha"]
        direction = "DESC" if descending else "ASC"
        expected = await scalar(
            installed,
            f"SELECT json_agg(l ORDER BY l {direction})::text FROM unnest(%s::text[]) AS l",
            labels,
        )
        expected_labels = json.loads(str(expected))
        assert expected_labels != sorted(labels, reverse=descending)

        answer, _ = await call_aggregate_function(
            installed,
            aggregate(
                {"fn": "count", "alias": "n"},
                group=["label"],
                order=[by("label", descending=descending)],
            ),
        )

        assert isinstance(answer, list)
        assert [row["label"] for row in answer] == expected_labels

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            pytest.param(
                by("qty"), [1, 2, 4, 8, 32, None], id="ascending-puts-null-last"
            ),
            pytest.param(
                by("qty", descending=True),
                [None, 32, 8, 4, 2, 1],
                id="descending-puts-null-first",
            ),
            pytest.param(
                by("qty", nulls="first"),
                [None, 1, 2, 4, 8, 32],
                id="ascending-nulls-first",
            ),
            pytest.param(
                by("qty", descending=True, nulls="last"),
                [32, 8, 4, 2, 1, None],
                id="descending-nulls-last",
            ),
        ],
    )
    async def test_orders_whole_numbers_with_the_nulls_of_postgresql(
        self, installed: Postgres, entry: dict[str, object], expected: list[int | None]
    ) -> None:
        """Orders by the number and not by its text, and places the nulls as PostgreSQL does."""
        answer, _ = await call_aggregate_function(
            installed,
            aggregate({"fn": "count", "alias": "n"}, group=["qty"], order=[entry]),
        )

        assert isinstance(answer, list)
        assert [row["qty"] for row in answer] == expected

    @pytest.mark.parametrize(
        ("column", "descending", "first"),
        [
            pytest.param("day", False, "2026-01-01", id="date-ascending"),
            pytest.param("day", True, "2026-06-01", id="date-descending"),
            pytest.param("flag", False, False, id="flag-false-first"),
            pytest.param("flag", True, True, id="flag-true-first"),
        ],
    )
    async def test_orders_dates_and_flags(
        self, installed: Postgres, column: str, descending: bool, first: object
    ) -> None:
        """Orders by the date and by the flag."""
        answer, _ = await call_aggregate_function(
            installed,
            aggregate(
                {"fn": "count", "alias": "n"},
                group=[column],
                order=[by(column, descending=descending)],
            ),
        )

        assert isinstance(answer, list)
        assert answer[0][column] == first

    async def test_orders_by_several_columns_and_names_the_alias(
        self, installed: Postgres
    ) -> None:
        """Uses the next column to break a tie, and the alias as the key of the value."""
        document = aggregate(
            total(),
            group=[("grp", "grupo"), "flag"],
            order=[by("grp", descending=True), by("flag")],
        )

        answer, _ = await call_aggregate_function(installed, document)

        assert answer == [
            {"grupo": "b", "flag": False, "n": None},
            {"grupo": "b", "flag": True, "n": 36},
            {"grupo": "a", "flag": False, "n": 2},
            {"grupo": "a", "flag": True, "n": 9},
        ]

    @pytest.mark.parametrize(
        ("limit", "offset", "expected"),
        [
            pytest.param(3, None, [1, 2, 4], id="limit"),
            pytest.param(2, 1, [2, 4], id="limit-and-offset"),
            pytest.param(None, 4, [32, None], id="offset-only"),
            pytest.param(None, 10, [], id="offset-past-the-end"),
            pytest.param(0, None, [], id="limit-zero"),
            pytest.param(100, 0, [1, 2, 4, 8, 32, None], id="limit-above-the-rows"),
        ],
    )
    async def test_cuts_the_ordered_rows(
        self,
        installed: Postgres,
        limit: int | None,
        offset: int | None,
        expected: list[int | None],
    ) -> None:
        """Skips the offset and keeps the limit, after the order."""
        document = aggregate(
            {"fn": "count", "alias": "n"},
            group=["qty"],
            order=[by("qty")],
            limit=limit,
            offset=offset,
        )

        answer, _ = await call_aggregate_function(installed, document)

        assert isinstance(answer, list)
        assert [row["qty"] for row in answer] == expected

    async def test_cuts_the_rows_without_an_order(self, installed: Postgres) -> None:
        """Keeps as many rows as the limit says, and gives an empty list when the offset skips them all."""
        count = {"fn": "count", "alias": "n"}

        limited, _ = await call_aggregate_function(
            installed, aggregate(count, group=["qty"], limit=2)
        )
        skipped, _ = await call_aggregate_function(
            installed, aggregate(count, group=["qty"], offset=6)
        )

        assert isinstance(limited, list)
        assert len(limited) == 2
        assert skipped == []


LABELS = ["Alpha", "Beta", "Zeta", "alpha", "zeta"]


@pytest.mark.postgres
class TestAggregateRowCap:
    """The function gives at most max_rows rows, and says so as PostgREST does when it cuts the answer."""

    async def test_cuts_the_groups_without_a_limit_and_asks_for_a_partial_answer(
        self, capped: Postgres
    ) -> None:
        """Give two of the five groups, with the status 206 and the range of the rows."""
        answer, headers = await call_aggregate_function(
            capped, aggregate({"fn": "count", "alias": "n"}, group=["label"])
        )

        assert isinstance(answer, list)
        assert len(answer) == 2
        assert await response_status(capped) == "206"
        assert headers["Content-Range"] == "0-1/*"

    async def test_keeps_the_source_headers_when_it_cuts(
        self, capped: Postgres
    ) -> None:
        """Send X-Source and the snapshot with the range."""
        _, headers = await call_aggregate_function(
            capped, aggregate({"fn": "count", "alias": "n"}, group=["label"])
        )

        assert headers["X-Source"] == "ducklake"
        assert headers["X-DuckLake-Snapshot"] == "7"

    async def test_cuts_after_the_order(self, capped: Postgres) -> None:
        """Give the first two groups of the order that PostgreSQL gives, and ask for a partial answer."""
        expected = await scalar(
            capped,
            "SELECT json_agg(l ORDER BY l)::text FROM (SELECT l FROM unnest(%s::text[]) AS l ORDER BY l LIMIT 2) s",
            LABELS,
        )
        document = aggregate(
            {"fn": "count", "alias": "n"},
            group=["label"],
            order=[{"column": "label", "descending": False}],
        )

        answer, headers = await call_aggregate_function(capped, document)

        assert isinstance(answer, list)
        assert [row["label"] for row in answer] == json.loads(str(expected))
        assert await response_status(capped) == "206"
        assert headers["Content-Range"] == "0-1/*"

    async def test_gives_the_range_of_the_rows_after_the_offset(
        self, capped: Postgres
    ) -> None:
        """Start the range at the offset."""
        document = aggregate(
            {"fn": "count", "alias": "n"},
            group=["label"],
            order=[{"column": "label", "descending": False}],
            offset=1,
        )

        answer, headers = await call_aggregate_function(capped, document)

        assert isinstance(answer, list)
        assert len(answer) == 2
        assert headers["Content-Range"] == "1-2/*"

    async def test_does_not_cut_when_the_rows_fit_in_the_cap(
        self, capped: Postgres
    ) -> None:
        """Give every group, without a partial status, when there are exactly as many as the cap."""
        answer, headers = await call_aggregate_function(
            capped, aggregate(total(), group=["grp"])
        )

        assert isinstance(answer, list)
        assert len(answer) == 2
        assert await response_status(capped) is None
        assert "Content-Range" not in headers

    async def test_does_not_cut_the_rows_after_the_last_one(
        self, capped: Postgres
    ) -> None:
        """Give the last two groups of five, with no partial status, because no row is left."""
        document = aggregate(
            {"fn": "count", "alias": "n"},
            group=["label"],
            order=[{"column": "label", "descending": False}],
            offset=3,
        )

        answer, _ = await call_aggregate_function(capped, document)

        assert isinstance(answer, list)
        assert len(answer) == 2
        assert await response_status(capped) is None

    async def test_gives_the_limit_of_the_request_without_a_partial_status(
        self, capped: Postgres
    ) -> None:
        """A request that asks for fewer rows than the cap gets them, and the rows that it did not ask for are no cut."""
        document = aggregate({"fn": "count", "alias": "n"}, group=["label"], limit=1)

        answer, headers = await call_aggregate_function(capped, document)

        assert isinstance(answer, list)
        assert len(answer) == 1
        assert await response_status(capped) is None
        assert "Content-Range" not in headers

    async def test_cuts_a_limit_above_the_cap(self, capped: Postgres) -> None:
        """Give the cap and not the limit, with the partial status, as PostgREST does with max-rows."""
        document = aggregate({"fn": "count", "alias": "n"}, group=["label"], limit=5)

        answer, headers = await call_aggregate_function(capped, document)

        assert isinstance(answer, list)
        assert len(answer) == 2
        assert await response_status(capped) == "206"
        assert headers["Content-Range"] == "0-1/*"


@pytest.mark.postgres
class TestAggregateWideTable:
    """A table with more than a hundred columns still gets an aggregate function."""

    async def test_orders_the_groups_of_a_table_with_many_columns(
        self, aggregate_function: Postgres
    ) -> None:
        """The function holds a list of column types, and a function call can't take that many values."""
        await install_aggregate_function(
            aggregate_function, has_rls=False, fallbacks=[], extra_columns=150
        )
        await put_state(aggregate_function, ROUTED_TABLE, FULL)
        await grant_everything(aggregate_function)
        document = aggregate(
            {"fn": "count", "alias": "n"}, group=["qty"], order=[by("qty")], limit=2
        )

        answer, _ = await call_aggregate_function(aggregate_function, document)

        assert answer == [{"qty": 1, "n": 1}, {"qty": 2, "n": 1}]


@pytest.mark.postgres
class TestAggregateDeniesByDefault:
    """A table with no row rules gives no row to a token that holds no wildcard."""

    @pytest.fixture
    async def open_table(self, aggregate_function: Postgres) -> Postgres:
        """Install the aggregate function for a table with no row rules, and grant nothing."""
        await install_aggregate_function(
            aggregate_function, has_rls=False, fallbacks=[]
        )
        await put_state(aggregate_function, ROUTED_TABLE, FULL)
        await set_setting(aggregate_function, "app.claim_sub", "u")
        return aggregate_function

    async def test_gives_an_empty_list_to_a_token_with_no_wildcard(
        self, open_table: Postgres
    ) -> None:
        """A subject with the schema in its claim and a unit grant still reads no row."""
        await insert_access_policy(open_table, "u", "unit", "u1")

        answer, headers = await call_aggregate_function(open_table, aggregate(total()))

        assert (answer, headers) == ([], {})

    async def test_sums_every_row_for_the_wildcard_grant(
        self, open_table: Postgres
    ) -> None:
        """The grant (*, *) of the subject reads the table."""
        await insert_access_policy(open_table, "u", "*", "*")

        answer, _ = await call_aggregate_function(open_table, aggregate(total()))

        assert answer == [{"n": 47}]


@pytest.mark.postgres
class TestAggregateRowRules:
    """The function applies the row rules of the table before it aggregates."""

    async def test_sums_only_the_units_of_the_grants(
        self, ruled_aggregate: Postgres
    ) -> None:
        """A subject with the unit u1 gets the sums of u1 only."""
        await grant_unit(ruled_aggregate, "u")

        answer, _ = await call_aggregate_function(
            ruled_aggregate, aggregate(total(), group=["grp"])
        )

        assert ordered(answer) == [{"grp": "a", "n": 3}, {"grp": "b", "n": 4}]

    async def test_gives_an_empty_list_without_a_grant(
        self, ruled_aggregate: Postgres
    ) -> None:
        """A subject with no grant gets a list with no item, and no header."""
        await set_setting(ruled_aggregate, "app.claim_sub", "nobody")

        answer, headers = await call_aggregate_function(
            ruled_aggregate, aggregate(total())
        )

        assert (answer, headers) == ([], {})

    @pytest.mark.parametrize(
        "schemas",
        [
            pytest.param("*", id="every-schema"),
            pytest.param("{schema}:*", id="the-schema"),
        ],
    )
    async def test_sums_every_row_for_a_wildcard_claim(
        self, ruled_aggregate: Postgres, schemas: str
    ) -> None:
        """A token for every row needs no grant."""
        answer, _ = await call_aggregate_function(
            ruled_aggregate,
            aggregate(total()),
            schemas=schemas.format(schema=ruled_aggregate.namespace.schema),
        )

        assert answer == [{"n": 47}]

    async def test_sums_every_row_for_the_wildcard_grant(
        self, ruled_aggregate: Postgres
    ) -> None:
        """A subject with the grant (*, *) reads every row of the schema."""
        await set_setting(ruled_aggregate, "app.claim_sub", "u")
        await insert_access_policy(ruled_aggregate, "u", "*", "*")

        answer, _ = await call_aggregate_function(ruled_aggregate, aggregate(total()))

        assert answer == [{"n": 47}]

    async def test_refuses_a_schema_outside_the_claim(
        self, ruled_aggregate: Postgres
    ) -> None:
        """A token for another schema gets the refusal that an unknown table gets, so it learns nothing about the table."""
        await grant_unit(ruled_aggregate, "u")

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            await call_aggregate_function(
                ruled_aggregate, aggregate(total()), schemas="other"
            )

        assert "no access to this table" in str(error.value)

    @pytest.mark.parametrize("role", ["dp_policy", "dp_ghost", ""])
    async def test_refuses_a_role_without_read_api(
        self, ruled_aggregate: Postgres, role: str
    ) -> None:
        """A role without read_api gets the same refusal as the table function."""
        with pytest.raises(psycopg.Error) as error:
            await call_aggregate_function(
                ruled_aggregate, aggregate(total()), role=role
            )

        assert error.value.sqlstate == "42501"


@pytest.mark.postgres
class TestAggregateFallback:
    """The function answers null for a request that the table function must serve."""

    @pytest.mark.parametrize(
        "document",
        [
            pytest.param(None, id="no-filter-header"),
            pytest.param({"columns": ["qty"]}, id="no-aggregate"),
            pytest.param(
                aggregate({"fn": "sum", "column": "grp", "alias": "n"}),
                id="sum-of-text",
            ),
            pytest.param(
                aggregate({"fn": "avg", "column": "grp", "alias": "n"}),
                id="average-of-text",
            ),
            pytest.param(
                aggregate({"fn": "avg", "column": "day", "alias": "n"}),
                id="average-of-a-date",
            ),
            pytest.param(
                aggregate({"fn": "avg", "column": "flag", "alias": "n"}),
                id="average-of-a-flag",
            ),
            pytest.param(
                aggregate({"fn": "avg", "alias": "n"}), id="average-without-a-column"
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "day", "alias": "n"}),
                id="sum-of-a-date",
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "flag", "alias": "n"}),
                id="sum-of-a-flag",
            ),
            pytest.param(
                aggregate({"fn": "max", "column": "flag", "alias": "n"}),
                id="max-of-a-flag",
            ),
            pytest.param(
                aggregate({"fn": "min", "column": "grp", "alias": "n"}),
                id="min-of-text",
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "ratio", "alias": "n"}),
                id="sum-of-a-double-differs-from-postgresql-in-the-last-digits",
            ),
            pytest.param(
                aggregate({"fn": "avg", "column": "ratio", "alias": "n"}),
                id="average-of-a-double-differs-from-postgresql-in-the-last-digits",
            ),
            pytest.param(
                aggregate({"fn": "avg", "column": "stamp", "alias": "n"}),
                id="average-of-a-timestamp",
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "stamp", "alias": "n"}),
                id="sum-of-a-timestamp",
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "nope", "alias": "n"}),
                id="unknown-column",
            ),
            pytest.param(
                aggregate({"fn": "sum", "alias": "n"}), id="sum-without-a-column"
            ),
            pytest.param(
                aggregate({"fn": "stddev", "column": "qty", "alias": "n"}),
                id="unknown-function",
            ),
            pytest.param(
                aggregate({"fn": "count", "column": "nope", "alias": "n"}),
                id="count-of-an-unknown-column",
            ),
            pytest.param(
                aggregate(total(), group=["tags"]), id="group-by-a-json-column"
            ),
            pytest.param(
                aggregate({"fn": "count", "column": "tags", "alias": "n"}),
                id="count-of-a-json-column",
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "tags", "alias": "n"}),
                id="sum-of-a-json-column",
            ),
            pytest.param(
                aggregate({"fn": "min", "column": "tags", "alias": "n"}),
                id="min-of-a-json-column",
            ),
            pytest.param(
                aggregate({"fn": "max", "column": "tags", "alias": "n"}),
                id="max-of-a-json-column",
            ),
            pytest.param(
                aggregate(total(), group=["ratio"]), id="group-by-a-decimal-column"
            ),
            pytest.param(
                aggregate(total(), group=["grp"], order=[by("qty")]),
                id="order-by-a-column-outside-the-group",
            ),
            pytest.param(
                aggregate(total(), group=[("grp", "g")], order=[by("g")]),
                id="order-by-the-alias-of-a-group-column",
            ),
            pytest.param(
                aggregate(total(), group=["grp"], order=[by("nope")]),
                id="order-by-an-unknown-column",
            ),
            pytest.param(
                aggregate(total(), group=["nope"]), id="group-by-an-unknown-column"
            ),
        ],
    )
    async def test_answers_null_for_a_shape_that_it_does_not_run(
        self, installed: Postgres, document: dict[str, object] | None
    ) -> None:
        """Answers null, so the proxy sends the request to the table."""
        answer, headers = await call_aggregate_function(installed, document)

        assert answer is None
        assert headers == {}

    @pytest.mark.parametrize(
        ("state", "fallbacks"),
        [
            pytest.param(
                partitioned(23, 25), ["bigquery"], id="bigquery-covers-the-rest"
            ),
            pytest.param(None, ["bigquery"], id="no-data-in-ducklake"),
        ],
    )
    async def test_answers_null_when_another_source_serves_a_part(
        self,
        aggregate_function: Postgres,
        state: TableState | None,
        fallbacks: list[str],
    ) -> None:
        """A table that a fallback source also serves goes to the table function."""
        await install_aggregate_function(
            aggregate_function, has_rls=False, fallbacks=fallbacks
        )
        await put_state(aggregate_function, ROUTED_TABLE, state)
        await grant_everything(aggregate_function)

        answer, _ = await call_aggregate_function(
            aggregate_function, aggregate(total())
        )

        assert answer is None

    async def test_runs_when_a_fallback_exists_but_ducklake_covers_everything(
        self, aggregate_function: Postgres
    ) -> None:
        """A fallback that nothing needs does not stop the function."""
        await install_aggregate_function(
            aggregate_function, has_rls=False, fallbacks=["bigquery"]
        )
        await put_state(aggregate_function, ROUTED_TABLE, FULL)
        await grant_everything(aggregate_function)

        answer, _ = await call_aggregate_function(
            aggregate_function, aggregate(total())
        )

        assert answer == [{"n": 47}]


COLUMNS = {
    "names": ["grp", "qty"],
    "kinds": ["text", "numeric"],
    "group": ["grp"],
    "count": ["grp", "qty"],
    "sum": ["qty"],
    "avg": ["qty"],
    "min": ["qty"],
    "max": ["qty"],
}


@pytest.mark.postgres
@pytest.mark.usefixtures("routing")
class TestPlanAggregateParts:
    """Each plan function gives the expressions of one part, or NULL for a request that it refuses."""

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param({}, [], id="no-group"),
            pytest.param(
                aggregate(group=["grp"]), ["grp AS grp"], id="an-allowed-column"
            ),
            pytest.param(
                aggregate(group=["qty"]), None, id="a-column-that-cannot-group"
            ),
            pytest.param(
                {"group": [{"column": "grp"}]}, None, id="a-group-column-without-alias"
            ),
        ],
    )
    async def test_group_gives_the_select_of_the_group_columns(
        self,
        postgres: Postgres,
        document: dict[str, object],
        expected: list[str] | None,
    ) -> None:
        """Gives a select for each group column that can group."""
        assert (
            await scalar(
                postgres,
                "SELECT data_proxy.plan_aggregate_group(%s::jsonb, %s::jsonb)",
                json.dumps(document.get("aggregate", document)),
                json.dumps(COLUMNS["group"]),
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param(aggregate(group=["grp"]), [], id="no-order"),
            pytest.param(
                aggregate(group=["grp"], order=[by("grp", descending=True)]),
                ["(t.elem ->> 'grp') DESC"],
                id="text-descending",
            ),
            pytest.param(
                aggregate(group=["grp"], order=[by("grp", nulls="last")]),
                ["(t.elem ->> 'grp') ASC NULLS LAST"],
                id="text-with-nulls-last",
            ),
            pytest.param(
                aggregate(group=["grp"], order=[by("qty")]),
                None,
                id="a-column-outside-the-group",
            ),
            pytest.param(
                aggregate(group=["qty"], order=[by("qty")]),
                None,
                id="a-type-that-sorts-differently",
            ),
        ],
    )
    async def test_order_gives_the_order_of_the_groups(
        self,
        postgres: Postgres,
        document: dict[str, object],
        expected: list[str] | None,
    ) -> None:
        """Gives an order only for a group column that PostgreSQL sorts like DuckDB."""
        assert (
            await scalar(
                postgres,
                "SELECT data_proxy.plan_aggregate_order(%s::jsonb, %s::jsonb)",
                json.dumps(document.get("aggregate", document)),
                json.dumps(COLUMNS),
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param(
                aggregate({"fn": "count", "alias": "n"}),
                ["count(*) AS n"],
                id="count-of-rows",
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "qty", "alias": "n"}),
                ["sum(qty) AS n"],
                id="sum-of-an-allowed-column",
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "grp", "alias": "n"}),
                None,
                id="sum-of-a-column-that-is-not-allowed",
            ),
            pytest.param(
                aggregate({"fn": "sum", "alias": "n"}), None, id="sum-without-a-column"
            ),
            pytest.param(
                aggregate({"fn": "median", "column": "qty", "alias": "n"}),
                None,
                id="an-unknown-function",
            ),
            pytest.param(
                aggregate({"fn": "count", "column": "qty"}),
                None,
                id="an-item-without-alias",
            ),
        ],
    )
    async def test_items_give_the_select_of_the_aggregates(
        self,
        postgres: Postgres,
        document: dict[str, object],
        expected: list[str] | None,
    ) -> None:
        """Gives an expression for each item that its function allows."""
        assert (
            await scalar(
                postgres,
                "SELECT data_proxy.plan_aggregate_items(%s::jsonb, %s::jsonb)",
                json.dumps(document.get("aggregate", document)),
                json.dumps(COLUMNS),
            )
            == expected
        )


VALUES = (
    "(VALUES ('a', 1, 1.10), ('a', 2, 2.20), ('b', 4, 3.30), ('a', 8, 4.40),"
    " ('b', NULL, NULL), ('b', 32, 6.60)) AS v(grp, qty, amount)"
)


@pytest.mark.postgres
class TestAggregateExactAverage:
    """The average is the sum divided by the count in PostgreSQL, so every digit equals the average of PostgreSQL."""

    @pytest.mark.parametrize("column", ["qty", "amount"])
    async def test_gives_the_average_that_postgresql_gives_for_each_group(
        self, installed: Postgres, column: str
    ) -> None:
        """Compare every digit with avg() over the same values, for a whole number and a decimal."""
        expected = await fetch_all_rows(
            installed,
            f"SELECT grp, avg({column}) AS media FROM {VALUES} GROUP BY grp",
        )

        answer, _ = await call_aggregate_function(
            installed,
            aggregate({"fn": "avg", "column": column, "alias": "media"}, group=["grp"]),
            exact=True,
        )

        assert ordered(answer) == ordered(expected)

    @pytest.mark.parametrize("column", ["qty", "amount"])
    async def test_gives_the_average_of_every_row_without_a_group(
        self, installed: Postgres, column: str
    ) -> None:
        """Skip the null, as avg() does."""
        expected = await fetch_all_rows(
            installed, f"SELECT avg({column}) AS media FROM {VALUES}"
        )

        answer, _ = await call_aggregate_function(
            installed,
            aggregate({"fn": "avg", "column": column, "alias": "media"}),
            exact=True,
        )

        assert answer == expected

    async def test_gives_null_for_a_group_without_a_value(
        self, installed: Postgres
    ) -> None:
        """A group where every value is null has no average, and a row of nulls is not zero."""
        document = aggregate(
            {"fn": "avg", "column": "qty", "alias": "media"}, group=["grp"]
        ) | {
            "where": {
                "kind": "comparison",
                "column": "unit_id",
                "operator": "eq",
                "negated": False,
                "value": "u2",
            }
        }

        answer, _ = await call_aggregate_function(installed, document, exact=True)

        assert ordered(answer) == ordered(
            [{"grp": "a", "media": Decimal(8)}, {"grp": "b", "media": None}]
        )

    async def test_gives_an_empty_list_for_the_average_of_no_row(
        self, installed: Postgres
    ) -> None:
        """No row gives no group, as the table does."""
        document = aggregate(
            {"fn": "avg", "column": "qty", "alias": "media"}, group=["grp"]
        ) | {
            "where": {
                "kind": "comparison",
                "column": "unit_id",
                "operator": "eq",
                "negated": False,
                "value": "nobody",
            }
        }

        answer, _ = await call_aggregate_function(installed, document, exact=True)

        assert answer == []

    async def test_gives_the_average_next_to_other_items_under_the_names_of_the_request(
        self, installed: Postgres
    ) -> None:
        """Keep the aliases, and leave no helper column in the answer."""
        answer, _ = await call_aggregate_function(
            installed,
            aggregate(
                {"fn": "avg", "column": "qty", "alias": "media"},
                {"fn": "sum", "column": "qty", "alias": "total"},
                {"fn": "count", "alias": "linhas"},
                group=["grp"],
            ),
            exact=True,
        )

        rows = TypeAdapter(list[dict[str, object]]).validate_python(answer)
        assert {tuple(sorted(row)) for row in rows} == {
            ("grp", "linhas", "media", "total")
        }

    async def test_orders_and_cuts_the_groups_that_hold_an_average(
        self, installed: Postgres
    ) -> None:
        """The order and the limit of a group column still apply."""
        answer, _ = await call_aggregate_function(
            installed,
            aggregate(
                {"fn": "avg", "column": "qty", "alias": "media"},
                group=["grp"],
                order=[by("grp", descending=True)],
                limit=1,
            ),
            exact=True,
        )

        rows = TypeAdapter(list[dict[str, object]]).validate_python(answer)
        assert [row["grp"] for row in rows] == ["b"]


@pytest.mark.postgres
class TestAggregateTimestamps:
    """The minimum and the maximum of a timestamp are the values that PostgreSQL gives, to the microsecond."""

    async def test_gives_the_minimum_and_the_maximum_of_a_timestamp(
        self, installed: Postgres
    ) -> None:
        """Compare with min() and max() over a timestamp column, as JSON."""
        expected = await scalar(
            installed,
            """
            SELECT json_build_object('low', min(t), 'high', max(t))::text
            FROM (VALUES
                (TIMESTAMP '2026-01-01 00:00:00.000001'),
                ('2026-01-01 00:00:10'), ('2026-01-01 00:00:20.123456'),
                ('2026-01-01 00:00:30'), (NULL), ('2026-01-01 00:00:40')
            ) AS v(t)
            """,
        )

        answer, _ = await call_aggregate_function(
            installed,
            aggregate(
                {"fn": "min", "column": "stamp", "alias": "low"},
                {"fn": "max", "column": "stamp", "alias": "high"},
            ),
        )

        assert answer == [json.loads(str(expected))]

    async def test_keeps_the_microsecond_of_the_minimum(
        self, installed: Postgres
    ) -> None:
        """A timestamp that differs in the last digit is not the same value."""
        answer, _ = await call_aggregate_function(
            installed, aggregate({"fn": "min", "column": "stamp", "alias": "low"})
        )

        assert answer == [{"low": "2026-01-01T00:00:00.000001"}]

    async def test_gives_the_maximum_of_a_timestamp_by_group(
        self, installed: Postgres
    ) -> None:
        """Group by a text column and read the latest time of each group."""
        answer, _ = await call_aggregate_function(
            installed,
            aggregate({"fn": "max", "column": "stamp", "alias": "high"}, group=["grp"]),
        )

        assert ordered(answer) == ordered(
            [
                {"grp": "a", "high": "2026-01-01T00:00:30"},
                {"grp": "b", "high": "2026-01-01T00:00:40"},
            ]
        )


@pytest.mark.postgres
class TestAggregateOrderInDuckDB:
    """A group column that sorts the same way in both engines is ordered and cut in DuckDB, so a table with millions of groups does not send them all to PostgreSQL."""

    @pytest.mark.parametrize(
        ("order", "expected"),
        [
            pytest.param(
                [by("qty")],
                "qty ASC NULLS LAST",
                id="whole-number-ascending-puts-nulls-last",
            ),
            pytest.param(
                [by("qty", descending=True)],
                "qty DESC NULLS FIRST",
                id="whole-number-descending-puts-nulls-first",
            ),
            pytest.param(
                [by("qty", nulls="first")],
                "qty ASC NULLS FIRST",
                id="nulls-first-as-asked",
            ),
            pytest.param(
                [by("day", descending=True), by("flag"), by("qty")],
                "day DESC NULLS FIRST, flag ASC NULLS LAST, qty ASC NULLS LAST",
                id="date-flag-and-whole-number",
            ),
        ],
    )
    async def test_orders_in_duckdb_with_the_nulls_that_postgresql_gives(
        self, installed: Postgres, order: list[dict[str, object]], expected: str
    ) -> None:
        """PostgreSQL ends an ascending order with the nulls and starts a descending one with them. DuckDB is asked for the same."""
        plan = await scalar(
            installed,
            "SELECT data_proxy.plan_aggregate_duckdb_order(%s::jsonb, %s::jsonb)",
            json.dumps(
                {
                    "group": [
                        {"column": name, "alias": name}
                        for name in ("qty", "day", "flag")
                        if any(entry["column"] == name for entry in order)
                    ],
                    "order": order,
                }
            ),
            json.dumps(
                {
                    "names": ["qty", "day", "flag", "label"],
                    "kinds": ["bigint", "date", "boolean", "text"],
                }
            ),
        )

        assert plan == expected

    @pytest.mark.parametrize(
        "order",
        [
            pytest.param([by("label")], id="text-sorts-by-collation"),
            pytest.param([by("qty"), by("label")], id="one-text-column-among-numbers"),
            pytest.param([by("ratio")], id="double-reaches-postgresql-as-a-double"),
            pytest.param([by("tags")], id="json"),
            pytest.param([by("nope")], id="unknown-column"),
            pytest.param([], id="no-order"),
            pytest.param(
                [by("qty")], id="the-order-misses-a-group-column-so-groups-can-tie"
            ),
        ],
    )
    async def test_keeps_the_order_in_postgresql_for_a_column_that_sorts_differently(
        self, installed: Postgres, order: list[dict[str, object]]
    ) -> None:
        """Without a DuckDB order, PostgreSQL orders and cuts, as before."""
        plan = await scalar(
            installed,
            "SELECT data_proxy.plan_aggregate_duckdb_order(%s::jsonb, %s::jsonb)",
            json.dumps(
                {
                    "group": [
                        {"column": "label", "alias": "label"},
                        {"column": "qty", "alias": "qty"},
                    ],
                    "order": order,
                }
            ),
            json.dumps(
                {
                    "names": ["qty", "label", "ratio", "tags"],
                    "kinds": ["bigint", "text", "double precision", "text"],
                }
            ),
        )

        assert plan is None


@pytest.mark.postgres
class TestAggregatePageFromDuckDB:
    """The page that DuckDB cuts equals the page that PostgreSQL cuts from every group."""

    @pytest.mark.parametrize("descending", [False, True])
    @pytest.mark.parametrize("nulls", [None, "first", "last"])
    @pytest.mark.parametrize(
        ("limit", "offset"),
        [
            pytest.param(None, None, id="every-group"),
            pytest.param(2, None, id="limit"),
            pytest.param(2, 2, id="limit-and-offset"),
            pytest.param(None, 3, id="offset-only"),
            pytest.param(1, 5, id="the-last-group"),
            pytest.param(3, 10, id="offset-past-the-end"),
        ],
    )
    async def test_gives_the_page_that_postgresql_gives(
        self,
        installed: Postgres,
        descending: bool,
        nulls: str | None,
        limit: int | None,
        offset: int | None,
    ) -> None:
        """Order by qty, which has a null group, and compare with the order of PostgreSQL over the same groups."""
        document = aggregate(
            {"fn": "count", "alias": "n"},
            group=["qty"],
            order=[by("qty", descending=descending, nulls=nulls)],
            limit=limit,
            offset=offset,
        )
        direction = "DESC" if descending else "ASC"
        null_order = (
            f" NULLS {nulls.upper()}"
            if nulls is not None
            else (" NULLS FIRST" if descending else " NULLS LAST")
        )
        skip = 0 if offset is None else offset
        take = "ALL" if limit is None else str(limit)
        expected = await scalar(
            installed,
            f"SELECT coalesce(json_agg(q ORDER BY rn), '[]')::text FROM ("
            f"SELECT qty, row_number() OVER (ORDER BY qty {direction}{null_order}) AS rn "
            f"FROM (VALUES (1),(2),(4),(8),(NULL::int),(32)) AS v(qty) "
            f"ORDER BY qty {direction}{null_order} LIMIT {take} OFFSET {skip}) q",
        )

        answer, _ = await call_aggregate_function(installed, document)

        assert isinstance(answer, list)
        assert [row["qty"] for row in answer] == [
            row["qty"] for row in json.loads(str(expected))
        ]
