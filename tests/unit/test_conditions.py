"""Unit tests for SQL predicate construction."""

import pytest

from data_proxy.conditions import partition_condition, schema_scope_condition
from data_proxy.sources.partitions import (
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from tests.helpers import partition


class TestSelectionConditions:
    """Selection predicate behavior tests."""

    @pytest.mark.parametrize(
        ("selection", "expected"),
        [
            pytest.param(
                RangeSelection(partition_id="10", column="id", lower=10, upper=20),
                '("id" >= 10 AND "id" < 20)',
                id="range-lower-inclusive-upper-exclusive",
            ),
            pytest.param(
                TimeRangeSelection(column="dt", lower="2025-01-01", upper="2025-01-02"),
                "(\"dt\" >= '2025-01-01' AND \"dt\" < '2025-01-02')",
                id="time-lower-inclusive-upper-exclusive",
            ),
            pytest.param(
                RemainderSelection(column="id", start=0, end=100),
                '("id" IS NULL OR "id" < 0 OR "id" >= 100)',
                id="remainder-null-or-outside-range",
            ),
        ],
    )
    def test_renders_exact_predicate(
        self,
        selection: RangeSelection | TimeRangeSelection | RemainderSelection,
        expected: str,
    ) -> None:
        """Render the exact predicate for each selection type."""
        assert (
            partition_condition(partition("10", selection)).as_string(None) == expected
        )

    def test_renders_schema_scope_claim(self) -> None:
        """Match the schema against the comma-separated schema claim."""
        assert schema_scope_condition("app").as_string(None) == (
            "'app' = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))"
        )
