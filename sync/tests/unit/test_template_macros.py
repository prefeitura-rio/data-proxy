"""Tests for the macros that render the lists of the SQL templates."""

import pytest

from sync.constants import SQL_DIR
from sync.templates import jinja_environment


def render(expression: str, **values: object) -> str:
    """Render one macro call of the macros file."""
    source = (
        '{% from "postgres/macros.sql" import sql_array, name_array %}' + expression
    )
    return jinja_environment(SQL_DIR).from_string(source).render(values)


class TestListMacros:
    """The macros give one array for a list of any length."""

    @pytest.mark.parametrize(
        ("columns", "expected"),
        [
            pytest.param([], "ARRAY[]::text[]", id="empty-list"),
            pytest.param([{"key": "'id'"}], "ARRAY['id']::text[]", id="one-item"),
            pytest.param(
                [{"key": "'id'"}, {"key": "'name'"}, {"key": "'day'"}],
                "ARRAY['id', 'name', 'day']::text[]",
                id="three-items",
            ),
        ],
    )
    def test_sql_array_joins_the_literals(
        self, columns: list[dict[str, str]], expected: str
    ) -> None:
        """Renders the literals as they are, in the order of the list."""
        assert (
            render("{{ sql_array(columns | map(attribute='key')) }}", columns=columns)
            == expected
        )

    def test_sql_array_keeps_only_the_selected_items(self) -> None:
        """Renders the keys of the columns that a filter selects."""
        columns = [
            {"key": "'id'", "sortable": True},
            {"key": "'blob'", "sortable": False},
            {"key": "'day'", "sortable": True},
        ]

        assert (
            render(
                "{{ sql_array(columns | selectattr('sortable') | map(attribute='key')) }}",
                columns=columns,
            )
            == "ARRAY['id', 'day']::text[]"
        )

    @pytest.mark.parametrize(
        ("names", "expected"),
        [
            pytest.param([], "ARRAY[]::text[]", id="empty-list"),
            pytest.param(["bigint"], "ARRAY['bigint']::text[]", id="one-item"),
            pytest.param(
                ["bigint", "text"], "ARRAY['bigint', 'text']::text[]", id="two-items"
            ),
        ],
    )
    def test_name_array_puts_each_name_in_quotes(
        self, names: list[str], expected: str
    ) -> None:
        """Renders each name as a text literal."""
        assert render("{{ name_array(names) }}", names=names) == expected
