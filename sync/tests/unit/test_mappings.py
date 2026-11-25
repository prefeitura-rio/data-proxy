from collections.abc import Mapping, Sequence

import pytest
from psycopg.sql import Literal
from pydantic import TypeAdapter

import sync.views.mappings as utils
from sync.models import FullTable, RlsRule, UnitMapping
from sync.types import TemplateValue


def decoded_mappings(
    mapping: Mapping[str, TemplateValue],
) -> list[dict[str, str | int | None]]:
    """Read the JSON list of unit mappings that the table function passes to rls_where."""
    literal = mapping["rls_mappings"]
    assert isinstance(literal, Literal)
    return TypeAdapter(list[dict[str, str | int | None]]).validate_json(
        literal.as_string(None).strip("'").replace("''", "'")
    )


class TestListColumns:
    """The rls rule of a list column."""

    @pytest.mark.parametrize(
        ("duckdb_type", "is_list"),
        [
            pytest.param("VARCHAR[]", True, id="list-of-text"),
            pytest.param("VARCHAR[][]", True, id="nested-list"),
            pytest.param("varchar[]", True, id="lower-case"),
            pytest.param("VARCHAR", False, id="text"),
            pytest.param("STRUCT(a VARCHAR)", False, id="struct"),
            pytest.param("VARCHAR[3]", False, id="fixed-size-array"),
        ],
    )
    def test_tells_a_list_column_from_a_scalar_one(
        self, duckdb_type: str, is_list: bool
    ) -> None:
        """A list column gets the list_has_any predicate in the rls rule."""
        assert utils.is_list(duckdb_type) is is_list

    @pytest.mark.parametrize(
        "duckdb_type",
        [
            pytest.param("VARCHAR[]", id="list-of-text"),
            pytest.param("VARCHAR", id="text"),
        ],
    )
    def test_passes_the_type_of_the_rls_column_to_the_macro(
        self, duckdb_type: str
    ) -> None:
        """The mapping of the table function carries the type, and the macro chooses the predicate."""
        table = FullTable(
            name="p.d.people",
            resolved_source="bigquery",
            rls=[RlsRule(conditions=[UnitMapping(column="area", unit_type="area")])],
        )

        mapping = utils.table_function_mapping(
            "app", table, [("area", duckdb_type)], "sub"
        )

        assert decoded_mappings(mapping) == [
            {"col": "area", "ut": "area", "ty": duckdb_type, "grp": None}
        ]

    def test_a_table_without_a_group_keeps_its_config_signature(self) -> None:
        """Leave the signature as it was, so no table rebuilds its data because of this release."""
        plain = FullTable(
            name="p.d.people",
            resolved_source="bigquery",
            rls=[RlsRule(conditions=[UnitMapping(column="area", unit_type="area")])],
        )

        assert plain.config_signature_fields()["rls"] == [
            {"column": "area", "unit_type": "area"}
        ]

    @pytest.mark.parametrize(
        ("rls", "expected"),
        [
            pytest.param(
                [{"conditions": [{"column": "a", "unit_type": "x"}]}],
                [("a", "x", "")],
                id="a-rule-of-one-condition-has-no-group",
            ),
            pytest.param(
                [{"column": "a", "unit_type": "x"}],
                [("a", "x", "")],
                id="a-flat-condition-reads-as-a-rule-of-one",
            ),
            pytest.param(
                [
                    {
                        "conditions": [
                            {"column": "a", "unit_type": "x"},
                            {"column": "b", "unit_type": "y"},
                        ]
                    }
                ],
                [("a", "x", "1"), ("b", "y", "1")],
                id="the-conditions-of-a-group-share-a-number",
            ),
            pytest.param(
                [
                    {
                        "conditions": [
                            {"column": "a", "unit_type": "x"},
                            {"column": "b", "unit_type": "y"},
                        ]
                    },
                    {"conditions": [{"column": "c", "unit_type": "z"}]},
                    {
                        "conditions": [
                            {"column": "d", "unit_type": "w"},
                            {"column": "e", "unit_type": "v"},
                        ]
                    },
                ],
                [
                    ("a", "x", "1"),
                    ("b", "y", "1"),
                    ("c", "z", ""),
                    ("d", "w", "2"),
                    ("e", "v", "2"),
                ],
                id="groups-are-numbered-by-position-from-one",
            ),
        ],
    )
    def test_numbers_the_groups_by_position_only_to_render_the_sql(
        self, rls: list[dict[str, object]], expected: list[tuple[str, str, str]]
    ) -> None:
        """The number is internal. It goes to the template and nowhere else."""
        table = FullTable.model_validate(
            {"name": "p.d.people", "resolved_source": "bigquery", "rls": rls}
        )
        columns: list[tuple[str, str]] = [(name, "VARCHAR") for name in "abcde"]

        mapping = utils.table_function_mapping("app", table, columns, "sub")

        assert [
            (
                entry["col"],
                entry["ut"],
                "" if entry["grp"] is None else str(entry["grp"]),
            )
            for entry in decoded_mappings(mapping)
        ] == expected

    def test_the_same_groups_give_the_same_signature_and_another_order_does_not(
        self,
    ) -> None:
        """Hash the nested shape, so a save of the same ways rebuilds nothing."""
        one = {
            "conditions": [
                {"column": "a", "unit_type": "x"},
                {"column": "b", "unit_type": "y"},
            ]
        }
        two = {"conditions": [{"column": "c", "unit_type": "z"}]}

        def signature(rls: Sequence[Mapping[str, object]]) -> object:
            table = FullTable.model_validate(
                {"name": "p.d.people", "resolved_source": "bigquery", "rls": list(rls)}
            )

            return table.config_signature_fields()["rls"]

        assert signature([one, two]) == signature([one, two])
        assert signature([one, two]) != signature([two, one])
        assert signature([two]) == [{"column": "c", "unit_type": "z"}]

    @pytest.mark.parametrize(
        "rule",
        [
            pytest.param({"conditions": []}, id="a-group-with-no-condition"),
            pytest.param({"conditions": "a"}, id="a-group-that-is-not-a-list"),
            pytest.param(
                {"column": "a", "unit_type": "x", "group": 1}, id="a-group-number"
            ),
            pytest.param(
                {"conditions": [{"column": "a", "unit_type": "x", "conditions": []}]},
                id="a-group-inside-a-group",
            ),
        ],
    )
    def test_refuses_a_rule_without_a_list_of_conditions(
        self, rule: dict[str, object]
    ) -> None:
        """Keep the shape small: a list of one condition or more, and no number."""
        with pytest.raises(
            ValueError, match=r"rls|conditions|group|Extra|at least|list"
        ):
            FullTable.model_validate(
                {"name": "p.d.people", "resolved_source": "bigquery", "rls": [rule]}
            )
