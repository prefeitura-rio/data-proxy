"""Behavior tests for fallback view planning and mappings."""

from unittest.mock import AsyncMock

import pytest

import data_proxy.fallback as fallback
from data_proxy.models import FullTable, SchemaConfig, SyncConfig


class TestFallbackMappings:
    """Fallback column and object mapping behavior."""

    def test_marks_nested_columns_as_json(self) -> None:
        """Map nested DuckDB columns to JSONB-compatible output."""
        columns = fallback.function_columns(
            [("payload", "STRUCT(id BIGINT)"), ("count", "BIGINT")],
            raw_json=False,
        )

        assert columns[0]["is_json"] is True
        assert columns[0]["pg_type"] == "text"
        assert columns[0]["return_type"] == "text"
        assert columns[1]["is_json"] is False
        assert columns[1]["pg_type"] == "bigint"

    def test_desires_fallback_view_only_when_configured(self) -> None:
        """Create a fallback view only for tables that enable it."""
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(
                    tables=[
                        FullTable(name="p.d.people", fallback=True),
                        FullTable(name="p.d.private"),
                    ]
                )
            }
        )

        assert fallback.desired_views(config) == {
            ("app", "people"),
            ("app", "people_bq"),
            ("app", "private"),
        }

    @pytest.mark.asyncio
    async def test_rejects_tables_without_discovered_columns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject a table when DuckDB returns no source columns."""
        monkeypatch.setattr(
            fallback,
            "column_types_from_duckdb",
            AsyncMock(return_value=[]),
        )

        with pytest.raises(RuntimeError, match="no columns"):
            await fallback.create_table_views(
                AsyncMock(),
                "app",
                FullTable(name="p.d.people"),
            )

    @pytest.mark.asyncio
    async def test_does_not_drop_protected_views(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep protected PostgreSQL views during reconciliation cleanup."""
        executor = AsyncMock()
        monkeypatch.setattr(fallback, "Executor", executor)

        await fallback.drop_removed_views(AsyncMock(), {("app", "access_policy")})

        executor.assert_not_called()
