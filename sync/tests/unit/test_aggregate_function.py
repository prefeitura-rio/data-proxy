"""Tests for the function that runs the aggregate of a request inside DuckDB."""

import json
from typing import Final

import psycopg
import pytest

from sync.models import Strategy, TableState
from tests.constants import ROUTED_TABLE
from tests.fixtures.types import Postgres
from tests.helpers import (
    call_aggregate_function,
    grant_unit,
    insert_access_policy,
    install_aggregate_function,
    partitioned,
    put_state,
    scalar,
    set_setting,
)

FULL: Final = TableState(strategy=Strategy.FULL, signature="s")


type GroupColumn = str | tuple[str, str]


def aggregate(
    *items: dict[str, str],
    group: list[GroupColumn] | None = None,
    order: list[dict[str, object]] | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, object]:
    """Build the filter document of an aggregate.

    A group column is a name, or a pair of the column and the alias.
    """
    body: dict[str, object] = {"items": list(items)}
    if group is not None:
        body["group"] = [
            {"column": entry, "alias": entry}
            if isinstance(entry, str)
            else {"column": entry[0], "alias": entry[1]}
            for entry in group
        ]
    tail = {"order": order, "limit": limit, "offset": offset}
    body.update({name: value for name, value in tail.items() if value is not None})
    return {"aggregate": body}


def by(
    column: str, *, descending: bool = False, nulls: str | None = None
) -> dict[str, object]:
    """Build one order entry."""
    return {"column": column, "descending": descending} | (
        {} if nulls is None else {"nulls": nulls}
    )


def total(alias: str = "n") -> dict[str, str]:
    """Build the aggregate item sum(qty)."""
    return {"fn": "sum", "column": "qty", "alias": alias}


def ordered(rows: object) -> list[dict[str, object]]:
    """Sort the rows by their text, because the function gives the groups in no order."""
    assert isinstance(rows, list)
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


@pytest.fixture
async def installed(aggregate_function: Postgres) -> Postgres:
    """Install the aggregate function for a table without row rules, and mark DuckLake as complete."""
    await install_aggregate_function(aggregate_function, has_rls=False, fallbacks=[])
    await put_state(aggregate_function, ROUTED_TABLE, FULL)
    return aggregate_function


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
                aggregate(
                    {"fn": "avg", "column": "ratio", "alias": "media"}, group=["grp"]
                ),
                [{"grp": "a", "media": 2.0}, {"grp": "b", "media": 4.0}],
                id="average-of-a-decimal-column-that-postgresql-sees-as-text",
            ),
            pytest.param(
                aggregate({"fn": "sum", "column": "ratio", "alias": "n"}),
                [{"n": 14.0}],
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
                aggregate(
                    {"fn": "avg", "column": "qty", "alias": "media"}, group=["grp"]
                ),
                [{"grp": "a", "media": 11 / 3}, {"grp": "b", "media": 18.0}],
                id="average-of-a-whole-number-column-ignores-null",
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
        document = aggregate(
            {"fn": "count", "alias": "n"}, group=["qty"], order=[by("qty")], limit=2
        )

        answer, _ = await call_aggregate_function(aggregate_function, document)

        assert answer == [{"qty": 1, "n": 1}, {"qty": 2, "n": 1}]


class TestAggregateRowRules:
    """The function applies the row rules of the table before it aggregates."""

    @pytest.fixture
    async def ruled(self, aggregate_function: Postgres) -> Postgres:
        """Install the aggregate function for a table with row rules."""
        await install_aggregate_function(aggregate_function, has_rls=True, fallbacks=[])
        await put_state(aggregate_function, ROUTED_TABLE, FULL)
        return aggregate_function

    async def test_sums_only_the_units_of_the_grants(self, ruled: Postgres) -> None:
        """A subject with the unit u1 gets the sums of u1 only."""
        await grant_unit(ruled, "u")

        answer, _ = await call_aggregate_function(
            ruled, aggregate(total(), group=["grp"])
        )

        assert ordered(answer) == [{"grp": "a", "n": 3}, {"grp": "b", "n": 4}]

    async def test_gives_an_empty_list_without_a_grant(self, ruled: Postgres) -> None:
        """A subject with no grant gets a list with no item, and no header."""
        await set_setting(ruled, "app.claim_sub", "nobody")

        answer, headers = await call_aggregate_function(ruled, aggregate(total()))

        assert (answer, headers) == ([], {})

    @pytest.mark.parametrize(
        "schemas",
        [
            pytest.param("*", id="every-schema"),
            pytest.param("{schema}:*", id="the-schema"),
        ],
    )
    async def test_sums_every_row_for_a_wildcard_claim(
        self, ruled: Postgres, schemas: str
    ) -> None:
        """A token for every row needs no grant."""
        answer, _ = await call_aggregate_function(
            ruled,
            aggregate(total()),
            schemas=schemas.format(schema=ruled.namespace.schema),
        )

        assert answer == [{"n": 47}]

    async def test_sums_every_row_for_the_wildcard_grant(self, ruled: Postgres) -> None:
        """A subject with the grant (*, *) reads every row of the schema."""
        await set_setting(ruled, "app.claim_sub", "u")
        await insert_access_policy(ruled, "u", "*", "*")

        answer, _ = await call_aggregate_function(ruled, aggregate(total()))

        assert answer == [{"n": 47}]

    async def test_gives_an_empty_list_for_a_schema_outside_the_claim(
        self, ruled: Postgres
    ) -> None:
        """A token for another schema gets no row."""
        await grant_unit(ruled, "u")

        answer, _ = await call_aggregate_function(
            ruled, aggregate(total()), schemas="other"
        )

        assert answer == []

    @pytest.mark.parametrize("role", ["dp_policy", "dp_ghost", ""])
    async def test_refuses_a_role_without_read_api(
        self, ruled: Postgres, role: str
    ) -> None:
        """A role without read_api gets the same refusal as the table function."""
        with pytest.raises(psycopg.Error) as error:
            await call_aggregate_function(ruled, aggregate(total()), role=role)

        assert error.value.sqlstate == "42501"


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

        answer, _ = await call_aggregate_function(
            aggregate_function, aggregate(total())
        )

        assert answer == [{"n": 47}]
