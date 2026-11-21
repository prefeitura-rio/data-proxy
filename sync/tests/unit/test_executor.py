"""The executor renders a template, runs it on its connection, and validates the rows of a query."""

import pytest
from psycopg.sql import Identifier
from pydantic import BaseModel, ValidationError

from sync import schema
from sync.executor import Executor
from sync.templates import render_template
from tests.fixtures.types import RecordedConnection


class Row(BaseModel):
    """The shape of the rows that the tests expect."""

    n: int


class TestExecute:
    """A statement runs as the template renders it, with the parameters of the caller."""

    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            pytest.param(None, None, id="no-parameters"),
            pytest.param(("a",), ("a",), id="with-parameters"),
        ],
    )
    async def test_runs_the_rendered_template_with_the_parameters(
        self, params: tuple[str, ...] | None, expected: tuple[str, ...] | None
    ) -> None:
        connection = RecordedConnection()
        mapping = {"schema": Identifier("app")}

        await Executor(conn=connection).execute(
            "postgres/notify_postgrest", mapping=mapping, params=params
        )

        assert connection.statements == [
            ("execute", render_template("postgres/notify_postgrest", mapping), expected)
        ]


class TestQuery:
    """The rows of a query come back as the type that the caller expects."""

    async def test_validates_each_row_as_the_expected_type(self) -> None:
        connection = RecordedConnection(rows=[{"n": 1}, {"n": 2}])

        rows = await Executor(conn=connection).query(
            "postgres/read_state", {"schema": Identifier("app")}, expect=Row
        )

        assert rows == [Row(n=1), Row(n=2)]

    @pytest.mark.parametrize(
        ("rows", "expect", "field"),
        [
            pytest.param([{"n": "x"}], Row, "n", id="a-field-of-another-type"),
            pytest.param([{"m": 1}], Row, "n", id="a-missing-field"),
            pytest.param([("a", "b", "c")], tuple[str, str], "", id="a-longer-tuple"),
            pytest.param([("a",)], tuple[str, str], "", id="a-shorter-tuple"),
            pytest.param([(1, "a")], tuple[str, str], "", id="a-number-for-a-text"),
        ],
    )
    async def test_refuses_a_row_of_another_shape(
        self, rows: list[object], expect: type, field: str
    ) -> None:
        connection = RecordedConnection(rows=rows)

        with pytest.raises(ValidationError, match=field):
            await Executor(conn=connection).query(
                "postgres/read_state", {"schema": Identifier("app")}, expect=expect
            )

    async def test_accepts_the_columns_that_a_tuple_names_as_any_object(self) -> None:
        connection = RecordedConnection(rows=[("a", "b", None, 1, "x", object())])

        rows = await Executor(conn=connection).query(
            "postgres/read_state",
            {"schema": Identifier("app")},
            expect=tuple[str, str, object, object, object, object],
        )

        assert len(rows) == 1


class TestNotifyPostgrest:
    """The reload message goes out after the schema change, and the transaction commits."""

    async def test_sends_the_reload_template_and_commits(self) -> None:
        connection = RecordedConnection()

        await schema.notify_postgrest(connection)  # type: ignore[arg-type]

        assert connection.statements == [
            (
                "execute",
                render_template(
                    "postgres/notify_postgrest", {"schema": schema.schema()}
                ),
                None,
            )
        ]
        assert connection.commits == 1
