"""Compare every aggregate that PostgREST accepts with the same aggregate over a typed native table.

Each case runs `select sum(column)` and the like through the view of the table, which PostgREST reads, and through
the aggregate function, which the proxy calls. Both must give what PostgreSQL gives for a native table with the same
values, or the test names the reason in `KNOWN_DIFFERENCES`.
"""

import json
from decimal import Decimal
from typing import Final

import psycopg
import pytest
from pydantic import TypeAdapter

from tests.constants import FULL, ROUTED_TABLE
from tests.fixtures.types import Postgres
from tests.helpers.core import scalar
from tests.helpers.routing import (
    act_as_token,
    aggregate,
    call_aggregate_function,
    grant_everything,
    install_aggregate_function,
    install_view_over_stub,
)
from tests.helpers.state import put_state

FUNCTIONS: Final = ("count", "sum", "avg", "min", "max")

# The columns of the stub table behind the function, with their DuckDB types.
COLUMNS: Final = {
    "unit_id": "VARCHAR",
    "grp": "VARCHAR",
    "qty": "BIGINT",
    "day": "DATE",
    "flag": "BOOLEAN",
    "ratio": "DOUBLE",
    "label": "VARCHAR",
    "amount": "DECIMAL(18,2)",
    "stamp": "TIMESTAMP",
}

# What PostgreSQL has no aggregate for. A request for one of these fails on a native table too.
NOT_IN_POSTGRESQL: Final = frozenset(
    {
        ("sum", "unit_id"),
        ("sum", "grp"),
        ("sum", "day"),
        ("sum", "flag"),
        ("sum", "label"),
        ("sum", "stamp"),
        ("avg", "unit_id"),
        ("avg", "grp"),
        ("avg", "day"),
        ("avg", "flag"),
        ("avg", "label"),
        ("avg", "stamp"),
        ("min", "flag"),
        ("max", "flag"),
    }
)

# What the function declines or answers in another form, with the reason. The proxy sends the request to PostgREST
# for each of them, and the view answers it, so the answer a client reads is the one of the native table.
KNOWN_DIFFERENCES: Final = {
    (
        "sum",
        "ratio",
    ): "A double sums to other last digits in DuckDB, and the view sums it in PostgreSQL.",
    (
        "avg",
        "ratio",
    ): "A double averages to other last digits in DuckDB, and the view averages it in PostgreSQL.",
    (
        "min",
        "unit_id",
    ): "Text sorts by bytes in DuckDB and by the collation in PostgreSQL.",
    (
        "max",
        "unit_id",
    ): "Text sorts by bytes in DuckDB and by the collation in PostgreSQL.",
    ("min", "grp"): "Text sorts by bytes in DuckDB and by the collation in PostgreSQL.",
    ("max", "grp"): "Text sorts by bytes in DuckDB and by the collation in PostgreSQL.",
    (
        "min",
        "label",
    ): "Text sorts by bytes in DuckDB and by the collation in PostgreSQL.",
    (
        "max",
        "label",
    ): "Text sorts by bytes in DuckDB and by the collation in PostgreSQL.",
}

ROWS: Final = (
    "(VALUES ('u1','a',1,DATE '2026-01-01',true,0.5,'Zeta',1.10,"
    " TIMESTAMP '2026-01-01 00:00:00.000001'),"
    " ('u1','a',2,DATE '2026-02-01',false,1.5,'alpha',2.20,TIMESTAMP '2026-01-01 00:00:10'),"
    " ('u1','b',4,DATE '2026-03-01',true,2.0,'Beta',3.30,TIMESTAMP '2026-01-01 00:00:20.123456'),"
    " ('u2','a',8,DATE '2026-04-01',true,4.0,'alpha',4.40,TIMESTAMP '2026-01-01 00:00:30'),"
    " ('u2','b',NULL,DATE '2026-05-01',false,NULL,'zeta',NULL,NULL),"
    " ('u3','b',32,DATE '2026-06-01',true,6.0,'Alpha',6.60,TIMESTAMP '2026-01-01 00:00:40'))"
    " AS v(unit_id, grp, qty, day, flag, ratio, label, amount, stamp)"
)


def expression(function: str, column: str) -> str:
    """Write the aggregate of a column."""
    return "count(*)" if column == "*" else f"{function}({column})"


@pytest.fixture
async def differential(aggregate_function: Postgres) -> Postgres:
    """Install the aggregate function and the view of the table over the stub rows, and a typed native table."""
    pg = aggregate_function
    await install_aggregate_function(pg, has_rls=False, fallbacks=[])
    await put_state(pg, ROUTED_TABLE, FULL)
    await grant_everything(pg)
    await pg.connection.execute(
        f"CREATE TEMP TABLE native AS SELECT * FROM {ROWS}".encode()
    )
    return pg


async def native(pg: Postgres, function: str, column: str, group: str | None) -> object:
    """Run the aggregate over the native table, and give the rows as the JSON that PostgREST writes."""
    select = "" if group is None else f"{group}, "
    tail = "" if group is None else f" GROUP BY {group}"
    cursor = await pg.connection.execute(
        (
            f"SELECT coalesce(json_agg(t)::text, '[]') FROM "
            f"(SELECT {select}{expression(function, column)} AS v FROM native{tail}) t"
        ).encode()
    )
    row = await cursor.fetchone()
    assert row is not None
    return json.loads(str(row[0]), parse_float=Decimal)


def normal(rows: object) -> list[dict[str, object]]:
    """Sort the groups by their text, and give a number as a Decimal, so two lists compare by value."""
    parsed = TypeAdapter(list[dict[str, object]]).validate_python(rows)

    def clean(value: object) -> object:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int | float | Decimal):
            return Decimal(str(value))
        return value

    cleaned = [{key: clean(value) for key, value in row.items()} for row in parsed]
    return sorted(cleaned, key=lambda row: json.dumps(row, sort_keys=True, default=str))


@pytest.mark.postgres
class TestAggregateAgainstTheNativeTable:
    """The aggregate function gives what a native table gives, for every function and every column."""

    @pytest.mark.parametrize("function", FUNCTIONS)
    @pytest.mark.parametrize("column", list(COLUMNS))
    async def test_gives_the_value_of_the_native_table_or_declines(
        self, differential: Postgres, function: str, column: str
    ) -> None:
        """The function answers with the value of the native table, or answers null so the proxy sends the request on."""
        if (function, column) in NOT_IN_POSTGRESQL:
            pytest.skip(
                "PostgreSQL has no such aggregate, so there is nothing to compare"
            )

        expected = await native(differential, function, column, None)
        answer, _ = await call_aggregate_function(
            differential,
            aggregate({"fn": function, "column": column, "alias": "v"}),
            exact=True,
        )

        if answer is None:
            assert (function, column) in KNOWN_DIFFERENCES, (
                f"{function}({column}) declined without a reason in KNOWN_DIFFERENCES"
            )
            return
        assert (function, column) not in KNOWN_DIFFERENCES, (
            f"{function}({column}) is listed as a difference, and the function answers it"
        )
        assert normal(answer) == normal(expected)

    @pytest.mark.parametrize("function", ["sum", "avg", "min", "max", "count"])
    @pytest.mark.parametrize("group", ["grp", "unit_id", "flag", "day"])
    async def test_gives_the_value_of_each_group(
        self, differential: Postgres, function: str, group: str
    ) -> None:
        """Group by a text, a date or a flag, and compare each group with the native table."""
        column = "qty"
        expected = await native(differential, function, column, group)
        answer, _ = await call_aggregate_function(
            differential,
            aggregate({"fn": function, "column": column, "alias": "v"}, group=[group]),
            exact=True,
        )

        assert answer is not None
        assert normal(answer) == normal(expected)

    async def test_counts_the_rows_with_no_column(self, differential: Postgres) -> None:
        """count() with no column counts every row, as count(*) does."""
        expected = await native(differential, "count", "*", None)
        answer, _ = await call_aggregate_function(
            differential, aggregate({"fn": "count", "alias": "v"}), exact=True
        )

        assert normal(answer) == normal(expected)

    @pytest.mark.parametrize(("function", "column"), sorted(KNOWN_DIFFERENCES))
    async def test_lists_only_differences_that_still_exist(
        self, differential: Postgres, function: str, column: str
    ) -> None:
        """A difference that stops existing leaves the list, so the list never grows stale."""
        answer, _ = await call_aggregate_function(
            differential,
            aggregate({"fn": function, "column": column, "alias": "v"}),
            exact=True,
        )

        assert answer is None, (
            f"{function}({column}) is answered now, so remove it from KNOWN_DIFFERENCES"
        )


@pytest.mark.postgres
class TestViewAgainstTheNativeTable:
    """The view of the table gives what a native table gives, so a request that leaves the function is correct."""

    @pytest.mark.parametrize("function", FUNCTIONS)
    @pytest.mark.parametrize("column", list(COLUMNS))
    async def test_gives_the_value_of_the_native_table(
        self, differential: Postgres, function: str, column: str
    ) -> None:
        """PostgREST aggregates the view in PostgreSQL, so every aggregate gives what the native table gives."""
        if (function, column) in NOT_IN_POSTGRESQL:
            pytest.skip(
                "PostgreSQL has no such aggregate, so there is nothing to compare"
            )
        await install_view_over_stub(differential)
        await act_as_token(differential, "dp_api", differential.namespace.schema)
        view = f"{differential.namespace.identifier.as_string(None)}.t_view"
        cursor = await differential.connection.execute(
            (
                f"SELECT coalesce(json_agg(t)::text, '[]') FROM "
                f"(SELECT {function}({column}) AS v FROM {view}) t"
            ).encode()
        )
        row = await cursor.fetchone()
        assert row is not None

        expected = await native(differential, function, column, None)

        assert normal(json.loads(str(row[0]), parse_float=Decimal)) == normal(expected)

    async def test_gives_the_column_types_of_a_native_table(
        self, differential: Postgres
    ) -> None:
        """A decimal, a double and a timestamp reach PostgreSQL with their own types, so PostgREST filters and orders them as numbers."""
        await install_view_over_stub(differential)
        await act_as_token(differential, "dp_api", differential.namespace.schema)
        view = f"{differential.namespace.identifier.as_string(None)}.t_view"

        types = await scalar(
            differential,
            f"SELECT concat_ws(',', pg_typeof(amount), pg_typeof(ratio), pg_typeof(stamp), pg_typeof(qty)) FROM {view} LIMIT 1",
        )

        assert types == "numeric,double precision,timestamp without time zone,bigint"

    async def test_filters_a_decimal_as_a_number(self, differential: Postgres) -> None:
        """amount > 10 compares numbers. As text, '4.40' is greater than '10', and the filter would pick the wrong rows."""
        await install_view_over_stub(differential)
        await act_as_token(differential, "dp_api", differential.namespace.schema)
        view = f"{differential.namespace.identifier.as_string(None)}.t_view"

        count = await scalar(
            differential, f"SELECT count(*) FROM {view} WHERE amount > 3"
        )
        expected = await scalar(
            differential, "SELECT count(*) FROM native WHERE amount > 3"
        )

        assert count == expected == 3


@pytest.fixture
def old_text_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """Map a decimal, a double and a timestamp to text, as the code did before the fix."""
    import sync.views.mappings as mappings

    original = mappings.pg_scalar_type

    def text_for_numbers(duckdb_type: str) -> str:
        base = original(duckdb_type)
        return "text" if base in {"numeric", "double precision", "timestamp"} else base

    monkeypatch.setattr(mappings, "pg_scalar_type", text_for_numbers)


@pytest.mark.postgres
@pytest.mark.usefixtures("old_text_types")
async def test_the_old_text_mapping_is_caught(differential: Postgres) -> None:
    """Prove the test can fail: with a decimal as text, PostgreSQL cannot compare it to a number, as it did before the fix."""
    await install_view_over_stub(differential)
    await act_as_token(differential, "dp_api", differential.namespace.schema)
    view = f"{differential.namespace.identifier.as_string(None)}.t_view"

    with pytest.raises(psycopg.errors.UndefinedFunction):
        await scalar(differential, f"SELECT count(*) FROM {view} WHERE amount > 3")
