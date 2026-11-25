"""The generic functions agg, hll and changes: one function for every table, with the table as an argument.

The wrappers of a data schema call the functions of the routing schema. Each function reads the facts of the table from
meta, so an unknown table, a table of another schema and a hostile name must all end in the same refusal, and no
text of the client may reach a SQL statement unquoted.
"""

import json

import psycopg.errors
import pytest

from tests.constants import FULL, ROUTED_TABLE
from tests.fixtures.types import Postgres
from tests.helpers.core import scalar, set_setting
from tests.helpers.routing import (
    act_as_token,
    aggregate,
    grant_everything,
    grant_unit,
    install_aggregate_function,
    total,
)
from tests.helpers.state import put_state

pytestmark = pytest.mark.postgres


async def call(pg: Postgres, sql: str, *params: object) -> object:
    """Call a wrapper as the token of the test and give its JSON as Python."""
    cursor = await pg.connection.execute(sql.encode(), params)
    row = await cursor.fetchone()
    assert row is not None
    return None if row[0] is None else json.loads(str(row[0]))


@pytest.fixture
async def served(aggregate_function: Postgres) -> Postgres:
    """Install the wrappers and the row of meta for the stub table, with the grant that reads every row."""
    await install_aggregate_function(aggregate_function, has_rls=False, fallbacks=[])
    await put_state(aggregate_function, ROUTED_TABLE, FULL)
    await grant_everything(aggregate_function)
    await act_as_token(
        aggregate_function, "dp_admin", aggregate_function.namespace.schema
    )
    return aggregate_function


@pytest.fixture
async def ruled(aggregate_function: Postgres) -> Postgres:
    """Install the wrappers for a table with a row rule on unit_id."""
    await install_aggregate_function(aggregate_function, has_rls=True, fallbacks=[])
    await put_state(aggregate_function, ROUTED_TABLE, FULL)
    await act_as_token(
        aggregate_function, "dp_admin", aggregate_function.namespace.schema
    )
    return aggregate_function


class TestTheTableArgument:
    """Every function refuses a table that the token cannot use, with one error that tells nothing."""

    @pytest.mark.parametrize(
        "table",
        [
            pytest.param("nothing", id="unknown-table"),
            pytest.param("T", id="name-in-another-case"),
            pytest.param("t; DROP TABLE x;--", id="statement-in-the-name"),
            pytest.param('t"', id="quote-in-the-name"),
            pytest.param("", id="empty-name"),
            pytest.param("t' OR '1'='1", id="literal-in-the-name"),
        ],
    )
    @pytest.mark.parametrize(
        "sql",
        [
            pytest.param("agg(%s)", id="agg"),
            pytest.param("hll(%s, 'qty')", id="hll"),
            pytest.param("changes(%s, 1, 2)", id="changes"),
        ],
    )
    async def test_refuses_a_table_that_table_meta_does_not_list(
        self, served: Postgres, sql: str, table: str
    ) -> None:
        """The answer is the refusal of an unknown table, and no statement of the name runs."""
        schema = served.namespace.schema

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            await served.connection.execute(
                f"SELECT * FROM {schema}.{sql}".encode(), (table,)
            )

        assert "no access to this table" in str(error.value)

    async def test_refuses_a_table_of_another_schema_with_the_same_refusal(
        self, served: Postgres
    ) -> None:
        """A token whose claim lacks the schema gets the answer of an unknown table, so it cannot tell the two apart."""
        schema = served.namespace.schema
        await act_as_token(served, "dp_api", "other")

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            await served.connection.execute(f"SELECT {schema}.agg('t')".encode())

        assert "no access to this table" in str(error.value)

    @pytest.mark.parametrize(
        ("sql", "role"),
        [
            pytest.param("agg('t')", "dp_policy", id="agg-needs-read_api"),
            pytest.param("hll('t', 'qty')", "dp_policy", id="hll-needs-read_api"),
            pytest.param(
                "changes('t', 1, 2)", "dp_api", id="changes-needs-read_ducklake"
            ),
        ],
    )
    async def test_refuses_a_role_without_the_permission(
        self, served: Postgres, sql: str, role: str
    ) -> None:
        """Each function checks its own permission before it reads the table."""
        schema = served.namespace.schema
        await act_as_token(served, role, schema)

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            await served.connection.execute(f"SELECT * FROM {schema}.{sql}".encode())

        assert "permission" in str(error.value)


class TestTheHllFunction:
    """The estimate of the distinct values of a column."""

    @pytest.mark.parametrize(
        "column",
        [
            pytest.param("tags", id="json-column"),
            pytest.param("ratio", id="double-column"),
            pytest.param("amount", id="decimal-column"),
            pytest.param("nothing", id="unknown-column"),
            pytest.param('qty"; DROP TABLE x;--', id="statement-in-the-column"),
        ],
    )
    async def test_refuses_a_column_that_cannot_group(
        self, served: Postgres, column: str
    ) -> None:
        """Only a text, whole-number, date or true-or-false column has an estimate."""
        schema = served.namespace.schema

        with pytest.raises(psycopg.errors.InvalidParameterValue):
            await served.connection.execute(
                f"SELECT {schema}.hll('t', %s)".encode(), (column,)
            )

    async def test_refuses_a_group_column_that_cannot_group(
        self, served: Postgres
    ) -> None:
        """The group_by column follows the same rule as the column."""
        schema = served.namespace.schema

        with pytest.raises(psycopg.errors.InvalidParameterValue):
            await served.connection.execute(
                f"SELECT {schema}.hll('t', 'qty', 'tags')".encode()
            )

    @pytest.mark.parametrize("lg_k", [0, 3, 22, 100, -1])
    async def test_refuses_a_precision_out_of_range(
        self, served: Postgres, lg_k: int
    ) -> None:
        """A sketch of lg_k outside 4 to 21 is not a sketch that the extension builds."""
        schema = served.namespace.schema

        with pytest.raises(psycopg.errors.InvalidParameterValue) as error:
            await served.connection.execute(
                f"SELECT {schema}.hll('t', 'qty', NULL, %s)".encode(), (lg_k,)
            )

        assert "lg_k" in str(error.value)

    async def test_gives_no_row_to_a_subject_without_a_grant(
        self, ruled: Postgres
    ) -> None:
        """A table with a row rule and a subject with no unit gets an empty list, as the aggregate function gives."""
        await set_setting(ruled, "app.claim_sub", "nobody")
        schema = ruled.namespace.schema

        answer = await call(ruled, f"SELECT {schema}.hll('t', 'unit_id')::text")

        assert answer == []

    async def test_estimates_the_distinct_values_of_a_column(
        self, served: Postgres
    ) -> None:
        """The six rows hold three distinct units. A sketch of a small set is exact."""
        schema = served.namespace.schema

        answer = await call(served, f"SELECT {schema}.hll('t', 'unit_id')::text")

        assert answer == [{"estimate": 3}]

    async def test_groups_the_estimate_by_a_column(self, served: Postgres) -> None:
        """One object for each group, in the order of the group column."""
        schema = served.namespace.schema

        answer = await call(served, f"SELECT {schema}.hll('t', 'unit_id', 'grp')::text")

        assert answer == [{"grp": "a", "estimate": 2}, {"grp": "b", "estimate": 3}]


class TestTheAggregateWrapper:
    """The wrapper gives the answer that the per-table function gave."""

    async def test_runs_the_aggregate_of_the_filter_header(
        self, served: Postgres
    ) -> None:
        """The proxy sends the header, and the function reads it for the table of the argument."""
        schema = served.namespace.schema
        await served.connection.execute(
            b"SELECT set_config('request.headers', %s, false)",
            (json.dumps({"x-ducklake-filter": json.dumps(aggregate(total()))}),),
        )

        answer = await call(served, f"SELECT {schema}.agg('t')::text")

        assert answer == [{"n": 47}]

    async def test_answers_null_without_an_aggregate(self, served: Postgres) -> None:
        """A request without an aggregate is for the table function, so the proxy sees a null."""
        schema = served.namespace.schema

        assert await scalar(served, f"SELECT {schema}.agg('t') IS NULL") is True

    async def test_keeps_the_rows_of_the_policy_for_a_unit_subject(
        self, ruled: Postgres
    ) -> None:
        """A subject with the unit u1 sums only the rows of u1."""
        schema = ruled.namespace.schema
        await grant_unit(ruled, "u")
        await ruled.connection.execute(
            b"SELECT set_config('request.headers', %s, false)",
            (json.dumps({"x-ducklake-filter": json.dumps(aggregate(total()))}),),
        )

        answer = await call(ruled, f"SELECT {schema}.agg('t')::text")

        assert answer == [{"n": 7}]
