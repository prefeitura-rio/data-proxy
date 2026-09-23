"""Tests for freshness publication decisions."""

from unittest.mock import AsyncMock

import pytest
from whenever import Instant

import data_proxy.freshness as freshness
from data_proxy.models import (
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    SyncPlan,
)
from tests.helpers import partition


class TestFailurePartitions:
    """FailurePartitions behavior tests."""

    def test_prefers_explicit_partitions(self) -> None:
        """Return partitions supplied for the table."""
        table = FullTable(name="p.app.people")
        assert freshness.failure_partitions(table, None, {table.name: ["10"]}) == ["10"]

    def test_preserves_empty_explicit_partitions(self) -> None:
        """Return an explicitly empty partition collection."""
        table = FullTable(name="p.app.people")
        assert freshness.failure_partitions(table, None, {table.name: []}) == []

    def test_ignores_explicit_partitions_for_another_table(self) -> None:
        """Ignore an override that belongs to another table."""
        table = PartitionedTable(name="p.app.people")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"10": partition("10")},
            changed_paths={"10": "path"},
            removed_partitions={},
        )
        assert freshness.failure_partitions(table, plan, {"p.app.other": ["20"]}) == {
            "10"
        }

    def test_returns_changed_partition_ids(self) -> None:
        """Return changed partition IDs for a partitioned table."""
        table = PartitionedTable(name="p.app.people")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"10": partition("10")},
            changed_paths={"10": "path"},
            removed_partitions={},
        )
        assert freshness.failure_partitions(table, plan, None) == {"10"}

    def test_returns_full_table_marker_by_default(self) -> None:
        """Return the full-table marker when no partition plan exists."""
        table = FullTable(name="p.app.people")
        assert freshness.failure_partitions(table, None, None) == {None}


class TestUpdatePublishedFreshness:
    """UpdatePublishedFreshness behavior tests."""

    @pytest.mark.asyncio
    async def test_records_full_table_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Delete old rows and record the full-table success."""
        delete = AsyncMock()
        upsert = AsyncMock()
        monkeypatch.setattr(freshness, "delete_table_freshness", delete)
        monkeypatch.setattr(freshness, "upsert_freshness", upsert)
        table = FullTable(name="p.app.people")
        await freshness.update_published_freshness(
            AsyncMock(),
            table,
            SyncPlan(schema_name="app"),
            set(),
            Instant.from_timestamp(0),
        )
        delete.assert_awaited_once()
        upsert.assert_awaited_once()
        assert upsert.await_args is not None
        assert upsert.await_args.args[2] == {None}

    @pytest.mark.asyncio
    async def test_records_incremental_success_failure_and_removal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record changed, failed, and removed partitions."""
        delete_table = AsyncMock()
        delete_partitions = AsyncMock()
        upsert = AsyncMock()
        monkeypatch.setattr(freshness, "delete_table_freshness", delete_table)
        monkeypatch.setattr(freshness, "delete_partition_freshness", delete_partitions)
        monkeypatch.setattr(freshness, "upsert_freshness", upsert)
        table = PartitionedTable(name="p.app.people")
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="s",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "path"},
                    removed_partitions={"5": partition("5")},
                )
            },
        )
        await freshness.update_published_freshness(
            AsyncMock(), table, plan, {"20"}, Instant.from_timestamp(0)
        )
        delete_table.assert_not_awaited()
        assert upsert.await_count == 2
        assert upsert.await_args_list[0].args[2] == {"10"}
        assert upsert.await_args_list[0].kwargs["success"] is True
        assert upsert.await_args_list[1].args[2] == {"20"}
        assert upsert.await_args_list[1].kwargs["success"] is False
        delete_partitions.assert_awaited_once()
        assert delete_partitions.await_args is not None
        assert delete_partitions.await_args.args[2] == {"5"}

    @pytest.mark.asyncio
    async def test_rebuild_deletes_previous_freshness(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Delete previous rows before recording a full rebuild."""
        delete = AsyncMock()
        upsert = AsyncMock()
        monkeypatch.setattr(freshness, "delete_table_freshness", delete)
        monkeypatch.setattr(freshness, "upsert_freshness", upsert)
        table = PartitionedTable(name="p.app.people")
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="s",
                    full_rebuild=True,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "path"},
                    removed_partitions={},
                )
            },
        )
        await freshness.update_published_freshness(
            AsyncMock(), table, plan, set(), Instant.from_timestamp(0)
        )
        delete.assert_awaited_once()
        upsert.assert_awaited_once()
