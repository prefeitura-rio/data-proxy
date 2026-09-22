"""Unit tests for fallback type and SQL mappings."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from data_proxy.fallback import (
    bq_function_mapping,
    bq_view_mapping,
    duckdb_type_for,
    is_nested_or_json,
    pg_scalar_type,
    quoted_identifier,
    return_type_for,
)
from data_proxy.models import FullTable, SchemaConfig, SyncConfig
from data_proxy.settings import Settings
from tests.strategies import identifiers


@dataclass(frozen=True, slots=True)
class FallbackColumn:
    """One BigQuery column and its expected fallback SQL representations."""

    name: str
    duckdb_type: str
    is_nested: bool


FALLBACK_COLUMNS = [
    FallbackColumn("data", "STRUCT(x VARCHAR)", True),
    FallbackColumn("items", "ARRAY(VARCHAR)", True),
    FallbackColumn("values", "LIST(VARCHAR)", True),
    FallbackColumn("payload", "JSON", True),
    FallbackColumn("name", "VARCHAR", False),
    FallbackColumn("born", "DATE", False),
    FallbackColumn("active", "BOOLEAN", False),
    FallbackColumn("count", "INTEGER", False),
    FallbackColumn("total", "BIGINT", False),
]


class TestNestedTypeDetection:
    """NestedTypeDetection behavior tests."""

    @given(column=st.sampled_from(FALLBACK_COLUMNS))
    def test_detects_nested_and_json_types(self, column: FallbackColumn) -> None:
        """Detect nested and JSON column types."""
        assert is_nested_or_json(column.duckdb_type) is column.is_nested

    def test_detects_lowercase_struct_type(self) -> None:
        """Detect a lowercase STRUCT type."""
        assert is_nested_or_json("struct(x int)")

    def test_detects_lowercase_json_type(self) -> None:
        """Detect a lowercase JSON type."""
        assert is_nested_or_json("json")


class TestFallbackTypeMapping:
    """FallbackTypeMapping behavior tests."""

    @given(
        case=st.sampled_from(
            [
                ("DATE", "date"),
                ("BOOLEAN", "boolean"),
                ("INTEGER", "bigint"),
                ("VARCHAR", "text"),
                ("STRUCT(x INT)", "text"),
            ]
        )
    )
    def test_maps_duckdb_types_to_postgres_types(self, case: tuple[str, str]) -> None:
        """Map DuckDB types to PostgreSQL types."""
        duckdb_type, expected = case
        assert return_type_for(duckdb_type) == expected
        assert pg_scalar_type(duckdb_type) == expected


type ColumnTypes = list[tuple[str, str]]
column_types = st.lists(
    st.tuples(
        identifiers,
        st.sampled_from(["VARCHAR", "INTEGER", "DATE", "STRUCT(x VARCHAR)", "JSON"]),
    ),
    min_size=1,
    max_size=4,
    unique_by=lambda item: item[0],
)


class TestFallbackMappingProperties:
    """Generated fallback mapping behavior tests."""

    @given(
        prefix=st.sampled_from(["STRUCT", "ARRAY", "LIST", "JSON"]),
        case=st.sampled_from([str.upper, str.lower, str.title]),
    )
    def test_detects_nested_type_with_mixed_case(
        self, prefix: str, case: Callable[[str], str]
    ) -> None:
        """Detect nested types independently of letter case."""
        assert is_nested_or_json(case(prefix))

    @given(
        prefix=st.sampled_from(["INTEGER", "BIGINT", "INT"]),
        suffix=st.from_regex("[A-Z0-9() ]{0,8}", fullmatch=True),
    )
    def test_maps_integer_family_to_bigint(self, prefix: str, suffix: str) -> None:
        """Map every integer family type to BIGINT."""
        assert pg_scalar_type(prefix + suffix) == "bigint"

    @given(pg_type=st.sampled_from(["jsonb", "text", "integer", "date"]))
    def test_maps_postgres_type_to_duckdb_type(self, pg_type: str) -> None:
        """Map PostgreSQL JSONB and scalar types for DuckDB."""
        expected = "json" if pg_type == "jsonb" else pg_type
        assert duckdb_type_for(pg_type) == expected

    @given(
        identifier=st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=1,
            max_size=20,
        )
    )
    def test_quotes_identifier(self, identifier: str) -> None:
        """Quote an identifier for generated SQL."""
        rendered = quoted_identifier(identifier)
        assert rendered.startswith('"')
        assert rendered.endswith('"')
        assert identifier.replace('"', '""') in rendered


class TestFallbackViewMapping:
    """Fallback view mapping behavior tests."""

    @given(columns=column_types)
    def test_marks_nested_view_columns_as_jsonb(self, columns: ColumnTypes) -> None:
        """Cast nested fallback columns to JSONB in the view mapping."""
        mapping = bq_view_mapping(
            "app", FullTable(name="p.app.people", resolved_schema="app"), columns
        )
        rendered = " ".join(cast("list[str]", mapping["columns"]))
        for column, duckdb_type in columns:
            if duckdb_type.startswith(("STRUCT", "JSON")):
                assert f'"{column}"::jsonb' in rendered
            else:
                assert f'"{column}"' in rendered

    @given(columns=column_types)
    def test_builds_function_mapping_for_configured_schema(
        self, columns: ColumnTypes
    ) -> None:
        """Build a fallback function mapping from table configuration."""
        config = SyncConfig(schemas={"app": SchemaConfig(claim="sub", tables=[])})

        def configured_sync_config(instance: Settings) -> SyncConfig:
            return config

        with patch.object(Settings, "sync_config", property(configured_sync_config)):
            table = FullTable(name="p.app.people", resolved_schema="app")
            mapping = bq_function_mapping("app", table, columns)
            assert mapping["claim_setting"] == "app.claim_sub"
            assert mapping["bq_table"] == table.name
            mapped_columns = cast("list[dict[str, object]]", mapping["columns"])
            assert len(mapped_columns) == len(columns)
            for mapped, (column, duckdb_type) in zip(
                mapped_columns, columns, strict=True
            ):
                assert mapped["name"] == f'"{column}"'
                assert mapped["key"] == f"'{column}'"
                assert mapped["is_json"] == is_nested_or_json(duckdb_type)
                assert mapped["pg_type"] == pg_scalar_type(duckdb_type)
                assert mapped["return_type"] == return_type_for(duckdb_type)
