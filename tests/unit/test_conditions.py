"""Unit tests for SQL predicate construction."""

from hypothesis import given

from data_proxy.conditions import (
    partition_condition,
    schema_scope_condition,
)
from data_proxy.models import (
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from tests.helpers import partition_for, render
from tests.strategies import (
    identifiers,
    range_selections,
    remainder_selections,
    time_selections,
)


class TestSelectionConditions:
    """Selection predicate behavior tests."""

    @given(selection=range_selections())
    def test_renders_range_bounds(self, selection: RangeSelection) -> None:
        """Render lower-inclusive and upper-exclusive range bounds."""
        rendered = render(partition_condition(partition_for(selection)))
        assert f'"{selection.column}"' in rendered
        assert str(selection.lower) in rendered
        assert str(selection.upper) in rendered

    @given(selection=time_selections())
    def test_renders_time_bounds(self, selection: TimeRangeSelection) -> None:
        """Render time-range bounds in the partition predicate."""
        rendered = render(partition_condition(partition_for(selection)))
        assert selection.lower in rendered
        assert selection.upper in rendered

    @given(selection=remainder_selections())
    def test_renders_remainder_null_and_range_checks(
        self, selection: RemainderSelection
    ) -> None:
        """Render null and out-of-range checks for a remainder."""
        rendered = render(partition_condition(partition_for(selection)))
        assert "IS NULL" in rendered
        assert str(selection.start) in rendered
        assert str(selection.end) in rendered

    @given(schema=identifiers)
    def test_renders_schema_scope_claim(self, schema: str) -> None:
        """Render a schema claim predicate for the selected schema."""
        rendered = render(schema_scope_condition(schema))
        assert schema in rendered
        assert "app.claim_schemas" in rendered
