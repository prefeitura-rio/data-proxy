"""Tests for Parquet-to-PostgreSQL loading operations."""

import pytest

from data_proxy.models import (
    PartitionedTablePlan,
    SyncPlan,
)
from data_proxy.publication import (
    reduce_sync_plan,
)
from tests.helpers import partition


class TestLoadingReduceIncremental:
    """Tests for ReduceIncremental behavior."""

    @pytest.mark.asyncio
    async def test_reduce_incremental_plan_keeps_failed_existing_partition(
        self,
    ) -> None:
        """
        GIVEN: a failed existing partition with a previous manifest entry.
        WHEN: reduce_sync_plan is called.
        THEN: the old manifest entry and path are kept and the partition is recorded as failed.
        """
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
    async def test_reduce_incremental_plan_omits_failed_new_partition(
        self,
    ) -> None:
        """
        GIVEN: a failed new partition without a previous manifest entry.
        WHEN: reduce_sync_plan is called.
        THEN: the partition is absent from the publication manifest.
        """
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
