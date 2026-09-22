"""Unit tests for publication decisions and state."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from psycopg import AsyncConnection

from data_proxy.conditions import partition_condition, scan_condition
from data_proxy.models import (
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    RemainderSelection,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
)
from data_proxy.publication import (
    CreateRoute,
    ReplacePartitionsRoute,
    ShadowSwapRoute,
    SyncContext,
    apply_partition_fallback,
    decide_route,
    empty_incremental_tables,
    failed_partition_ids,
    planned_paths,
    reduce_sync_plan,
)
from tests.helpers import partition


class TestPublicationValidation:
    """PublicationValidation behavior tests."""

    def test_rejects_invalid_partition_plan(
        self, invalid_partition_plan: PartitionedTablePlan
    ) -> None:
        """Reject an invalid partition plan."""
        with pytest.raises(AssertionError):
            planned_paths(SyncPlan(schema_name="app"), "p.d.t", invalid_partition_plan)

    def test_rejects_invalid_partition_selection(
        self, invalid_physical_partition: PhysicalPartition
    ) -> None:
        """Reject an invalid partition selection."""
        with pytest.raises(AssertionError):
            partition_condition(invalid_physical_partition)


class TestPartitionConditions:
    """PartitionConditions behavior tests."""

    def test_includes_null_and_out_of_range_values(self) -> None:
        """Include null and out-of-range values in the remainder condition."""
        remainder = PhysicalPartition(
            partition_id="__NULL__",
            signature="signature",
            selection=RemainderSelection(column="cpf", start=0, end=100),
        )
        rendered = partition_condition(remainder).as_string(None)
        assert '"cpf" IS NULL' in rendered
        assert '"cpf" >= 100' in rendered

    def test_uses_parquet_record_column(self) -> None:
        """Use the Parquet record column in the scan condition."""
        rendered = scan_condition(partition("10")).as_string(None)
        assert "r['cpf']" in rendered


class TestSyncPlanReduction:
    """SyncPlanReduction behavior tests."""

    def test_keeps_plan_without_failed_partitions(self) -> None:
        """Keep a plan without failed partitions."""
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.people": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "ok"},
                    removed_partitions={},
                )
            },
        )
        decision = reduce_sync_plan(plan, set())
        assert decision.blocked_tables == set()
        assert decision.failed_partitions == {}

    def test_blocks_full_rebuild_after_partition_failure(self) -> None:
        """Block a full rebuild after a partition failure."""
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.people": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "failed"},
                    removed_partitions={},
                )
            },
        )
        decision = reduce_sync_plan(plan, {"failed"})
        assert decision.blocked_tables == {"p.app.people"}


class TestPublicationRoutes:
    """PublicationRoutes behavior tests."""

    def test_uses_shadow_swap_for_existing_full_table(self) -> None:
        """Use a shadow swap for an existing full table."""
        table = FullTable(name="p.d.t")
        assert decide_route(True, table, None) == ShadowSwapRoute()

    def test_uses_create_for_new_full_table(self) -> None:
        """Use create for a new full table."""
        table = FullTable(name="p.d.t")
        assert decide_route(False, table, None) == CreateRoute()

    def test_uses_partition_replacement_for_incremental_table(self) -> None:
        """Use partition replacement for an incremental table."""
        table = PartitionedTable(name="p.d.t")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        assert decide_route(True, table, plan) == ReplacePartitionsRoute(plan=plan)

    def test_uses_shadow_swap_for_partitioned_full_rebuild(self) -> None:
        """Use a shadow swap for a partitioned full rebuild."""
        table = PartitionedTable(name="p.d.t")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=True,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        assert decide_route(True, table, plan) == ShadowSwapRoute()

    def test_uses_create_for_new_partitioned_table(self) -> None:
        """Use create for a new partitioned table."""
        table = PartitionedTable(name="p.d.t")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=True,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        assert decide_route(False, table, plan) == CreateRoute()


class TestIncrementalPlanReduction:
    """IncrementalPlanReduction behavior tests."""

    @pytest.mark.asyncio
    async def test_preserves_previous_partition_when_replacement_fails(self) -> None:
        """Preserve a previous partition when replacement fails."""
        previous = partition("10")
        current = previous.model_copy(update={"signature": "new"})
        successful = partition("20")
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.people": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": current, "20": successful},
                    changed_paths={"10": "failed", "20": "successful"},
                    previous_partitions={"10": previous},
                    removed_partitions={},
                )
            },
        )
        decision = reduce_sync_plan(plan, {"failed"})
        reduced = decision.plan
        blocked = decision.blocked_tables
        failures = decision.failed_partitions
        table_plan = reduced.partitioned_tables["p.app.people"]
        assert blocked == set()
        assert failures == {"p.app.people": {"10"}}
        assert table_plan.changed_paths == {"20": "successful"}
        assert table_plan.current_partitions == {"10": previous, "20": successful}

    @pytest.mark.asyncio
    async def test_omits_failed_new_partition(self) -> None:
        """Omit a new partition that fails."""
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.people": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "failed"},
                    removed_partitions={},
                )
            },
        )
        decision = reduce_sync_plan(plan, {"failed"})
        reduced = decision.plan
        blocked = decision.blocked_tables
        assert blocked == set()
        assert reduced.partitioned_tables["p.app.people"].current_partitions == {}


class TestPartitionFailureHelpers:
    """Partition failure helper behavior tests."""

    @given(failed=st.sets(st.sampled_from(["1", "2"])))
    def test_finds_failed_partition_ids(self, failed: set[str]) -> None:
        """Find partition IDs whose paths failed."""
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"1": partition("1"), "2": partition("2")},
            changed_paths={"1": "path-1", "2": "path-2"},
            removed_partitions={},
        )
        assert failed_partition_ids(plan, {f"path-{item}" for item in failed}) == failed

    def test_keeps_previous_partition_when_replacement_fails(self) -> None:
        """Restore the previous partition after a failed replacement."""
        previous = partition("1", "old")
        current = partition("1", "new")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"1": current},
            changed_paths={"1": "path"},
            previous_partitions={"1": previous},
            removed_partitions={},
        )
        apply_partition_fallback(plan, {"1"})
        assert plan.current_partitions["1"] == previous
        assert plan.changed_paths == {}

    def test_removes_failed_new_partition(self) -> None:
        """Remove a failed partition with no previous manifest entry."""
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"1": partition("1")},
            changed_paths={"1": "path"},
            removed_partitions={},
        )
        apply_partition_fallback(plan, {"1"})
        assert plan.current_partitions == {}
        assert plan.changed_paths == {}


class TestPublicationPlanHelpers:
    """Publication plan helper behavior tests."""

    @given(path=st.from_regex("s3://[a-z]+/[a-z]+", fullmatch=True))
    def test_removes_duplicate_partition_paths(self, path: str) -> None:
        """Return each partition path once while preserving order."""
        partitioned = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"1": partition("1"), "2": partition("2")},
            changed_paths={"1": path, "2": path},
            removed_partitions={},
        )
        plan = SyncPlan(schema_name="d", partitioned_tables={"p.d.t": partitioned})
        assert planned_paths(plan, "p.d.t", partitioned) == [path]

    @given(failed=st.sets(st.sampled_from(["path", "unrelated"])))
    def test_blocks_failed_ordinary_table(self, failed: set[str]) -> None:
        """Block an ordinary table when one of its paths fails."""
        plan = SyncPlan(
            schema_name="d", signatures={"p.d.t": "s"}, paths={"p.d.t": ["path"]}
        )
        decision = reduce_sync_plan(plan, failed)
        expected: set[str] = {"p.d.t"} if "path" in failed else set()
        assert decision.blocked_tables == expected

    def test_blocks_failed_full_rebuild(self) -> None:
        """Block a full partition rebuild when a path fails."""
        partitioned = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=True,
            current_partitions={"1": partition("1")},
            changed_paths={"1": "path"},
            removed_partitions={},
        )
        plan = SyncPlan(schema_name="d", partitioned_tables={"p.d.t": partitioned})
        assert reduce_sync_plan(plan, {"path"}).blocked_tables == {"p.d.t"}

    def test_applies_partition_fallback_idempotently(self) -> None:
        """Keep the same state when fallback is applied twice."""
        previous = partition("1", "old")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"1": partition("1", "new")},
            changed_paths={"1": "path"},
            previous_partitions={"1": previous},
            removed_partitions={},
        )
        apply_partition_fallback(plan, {"1"})
        state = plan.model_dump()
        apply_partition_fallback(plan, {"1"})
        assert plan.model_dump() == state

    def test_finds_empty_incremental_tables(self) -> None:
        """Find incremental tables without publishable changes."""
        plan = SyncPlan(
            schema_name="d",
            partitioned_tables={
                "p.d.empty": PartitionedTablePlan(
                    table_signature="s",
                    full_rebuild=False,
                    current_partitions={},
                    changed_paths={},
                    removed_partitions={},
                ),
                "p.d.changed": PartitionedTablePlan(
                    table_signature="s",
                    full_rebuild=False,
                    current_partitions={"1": partition("1")},
                    changed_paths={"1": "path"},
                    removed_partitions={},
                ),
            },
        )
        assert empty_incremental_tables(plan) == {"p.d.empty"}


class TestSyncContextLookup:
    """Sync context lookup behavior tests."""

    def test_indexes_configured_tables_by_name(
        self, async_connection_double: AsyncConnection
    ) -> None:
        """Index every configured table by its source name."""
        config = SyncConfig(
            schemas={
                "d": SchemaConfig(
                    tables=[
                        FullTable(name="p.d.full"),
                        PartitionedTable(name="p.d.partitioned"),
                    ]
                )
            }
        )
        context = SyncContext(
            pg_conn=async_connection_double,
            dbos_conn=async_connection_double,
            config=config,
            plan=SyncPlan(schema_name="d"),
        )
        assert set(context.tables_by_name) == {"p.d.full", "p.d.partitioned"}


class TestSyncContextValidation:
    """Sync context validation behavior tests."""

    def test_calculates_blocked_empty_and_eligible_tables(
        self, async_connection_double: AsyncConnection
    ) -> None:
        """Calculate blocked, empty, and eligible tables from a plan."""
        config = SyncConfig(
            schemas={
                "d": SchemaConfig(
                    tables=[
                        FullTable(name="p.d.full"),
                        PartitionedTable(name="p.d.empty"),
                        PartitionedTable(name="p.d.changed"),
                    ]
                )
            }
        )
        empty = PartitionedTablePlan(
            table_signature="empty",
            full_rebuild=False,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        changed = PartitionedTablePlan(
            table_signature="changed",
            full_rebuild=False,
            current_partitions={"1": partition("1")},
            changed_paths={"1": "changed-path"},
            removed_partitions={},
        )
        plan = SyncPlan(
            schema_name="d",
            signatures={"p.d.full": "full"},
            paths={"p.d.full": ["full-path"]},
            partitioned_tables={"p.d.empty": empty, "p.d.changed": changed},
        )
        context = SyncContext(
            pg_conn=async_connection_double,
            dbos_conn=async_connection_double,
            config=config,
            plan=plan,
            failed_paths={"full-path"},
        )
        context.validate()
        assert context.decision is not None
        assert context.decision.blocked_tables == {"p.d.full"}
        assert context.empty_incremental == {"p.d.empty"}
        assert context.eligible == {"p.d.changed"}
