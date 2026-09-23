"""Tests for persisted synchronization state reads."""

from unittest.mock import AsyncMock

import pytest

import data_proxy.state as state
from data_proxy.models import PartitionManifest, Strategy, TableState


class TestReadTableState:
    """ReadTableState behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_none_when_state_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return none for a missing table."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = None
        monkeypatch.setattr(state, "execute_sql", AsyncMock(return_value=cursor))
        assert await state.read_table_state(AsyncMock(), "p.app.people") is None

    @pytest.mark.asyncio
    async def test_decodes_table_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Decode a stored table state."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = (
            '{"strategy":"full","signature":"s","partitions":null}',
        )
        monkeypatch.setattr(state, "execute_sql", AsyncMock(return_value=cursor))
        result = await state.read_table_state(AsyncMock(), "p.app.people")
        assert result == TableState(strategy=Strategy.FULL, signature="s")


class TestReadPartitionManifest:
    """ReadPartitionManifest behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_none_without_partitions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return none for full-table state."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = (
            '{"strategy":"full","signature":"s","partitions":null}',
        )
        monkeypatch.setattr(state, "execute_sql", AsyncMock(return_value=cursor))
        assert await state.read_partition_manifest(AsyncMock(), "p.app.people") is None

    @pytest.mark.asyncio
    async def test_builds_manifest_from_partitioned_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Build a partition manifest from stored state."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = (
            '{"strategy":"partitioned","signature":"s","partitions":{}}',
        )
        monkeypatch.setattr(state, "execute_sql", AsyncMock(return_value=cursor))
        result = await state.read_partition_manifest(AsyncMock(), "p.app.people")
        assert result == PartitionManifest(table_signature="s", partitions={})
