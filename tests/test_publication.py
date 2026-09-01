"""Tests for publication input validation."""

import pytest

from dp.models import PartitionedTablePlan, PhysicalPartition, SyncPlan
from dp.publication import partition_predicate, planned_paths


class TestPublication:
    """Tests for publication input validation."""

    def test_planned_paths_rejects_an_invalid_partition_plan(
        self,
        invalid_partition_plan: PartitionedTablePlan,
    ) -> None:
        """
        GIVEN: an invalid partition plan value.
        WHEN: planned_paths is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            planned_paths(
                SyncPlan(schema_name="app"),
                "p.d.t",
                invalid_partition_plan,
            )

    def test_partition_predicate_rejects_an_invalid_selection_type(
        self,
        invalid_physical_partition: PhysicalPartition,
    ) -> None:
        """
        GIVEN: a physical partition with an invalid selection type.
        WHEN: partition_predicate is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            partition_predicate(invalid_physical_partition)
