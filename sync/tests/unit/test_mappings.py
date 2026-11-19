import pytest

import sync.views.mappings as utils
from sync.models import FullTable, UnitMapping


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
            rls=[UnitMapping(column="area", unit_type="area")],
        )

        mapping = utils.table_function_mapping(
            "app", table, [("area", duckdb_type)], "sub"
        )

        assert mapping["rls_mappings"] == [
            {
                "column": "area",
                "unit_type": "area",
                "duckdb_type": duckdb_type,
                "group": "",
            }
        ]

    @pytest.mark.parametrize(
        ("group", "expected"),
        [
            pytest.param(None, "", id="no-group"),
            pytest.param(1, "1", id="group-one"),
            pytest.param(12, "12", id="group-twelve"),
        ],
    )
    def test_passes_the_group_of_an_rls_rule_as_text(
        self, group: int | None, expected: str
    ) -> None:
        """The template tells a rule with no group from a rule in a group by the empty text."""
        table = FullTable(
            name="p.d.people",
            resolved_source="bigquery",
            rls=[UnitMapping(column="area", unit_type="area", group=group)],
        )

        mapping = utils.table_function_mapping(
            "app", table, [("area", "VARCHAR")], "sub"
        )

        assert mapping["rls_mappings"] == [
            {
                "column": "area",
                "unit_type": "area",
                "duckdb_type": "VARCHAR",
                "group": expected,
            }
        ]

    @pytest.mark.parametrize("group", [0, -1])
    def test_refuses_a_group_below_one(self, group: int) -> None:
        """A group is a positive number, so the sync config stops on a wrong value."""
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            UnitMapping(column="area", unit_type="area", group=group)

    def test_a_table_without_a_group_keeps_its_config_signature(self) -> None:
        """Leave the signature as it was, so no table rebuilds its data because of this release."""
        plain = FullTable(
            name="p.d.people",
            resolved_source="bigquery",
            rls=[UnitMapping(column="area", unit_type="area")],
        )

        assert plain.config_signature_fields()["rls"] == [
            {"column": "area", "unit_type": "area"}
        ]

    def test_a_group_changes_the_config_signature(self) -> None:
        """Rebuild the function when a rule joins a group."""
        plain = FullTable(
            name="p.d.people",
            resolved_source="bigquery",
            rls=[UnitMapping(column="area", unit_type="area")],
        )
        grouped = FullTable(
            name="p.d.people",
            resolved_source="bigquery",
            rls=[UnitMapping(column="area", unit_type="area", group=1)],
        )

        assert plain.config_signature_fields() != grouped.config_signature_fields()

    @pytest.mark.parametrize(
        "duckdb_type",
        [
            pytest.param("BIGINT[]", id="numbers"),
            pytest.param("VARCHAR[][]", id="nested"),
        ],
    )
    def test_rejects_a_list_column_that_is_not_a_list_of_text(
        self, duckdb_type: str
    ) -> None:
        """A grant holds text, so another element type cannot match and the sync stops with a clear message."""
        table = FullTable(
            name="p.d.people",
            resolved_source="bigquery",
            rls=[UnitMapping(column="area", unit_type="area")],
        )

        with pytest.raises(ValueError, match="must be VARCHAR"):
            utils.table_function_mapping("app", table, [("area", duckdb_type)], "sub")

    def test_rejects_an_rls_column_that_the_source_lacks(self) -> None:
        """A rule on a missing column would build a predicate that fails at the first read, so the sync stops."""
        table = FullTable(
            name="p.d.people",
            resolved_source="bigquery",
            rls=[UnitMapping(column="area", unit_type="area")],
        )

        with pytest.raises(ValueError, match="not a column"):
            utils.table_function_mapping("app", table, [("other", "VARCHAR")], "sub")
