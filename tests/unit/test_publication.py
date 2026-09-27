"""Unit tests for DuckLake publication planning logic."""

from data_proxy.ducklake import (
    empty_incremental_tables,
    failed_partition_ids,
    plan_publication,
    planned_paths,
    restore_partition,
)
from data_proxy.models import (
    PartitionChange,
    PartitionedTablePlan,
    PhysicalPartition,
)
from tests.helpers import partition, sync_plan


def table_plan(
    *,
    full_rebuild: bool = False,
    current: dict[str, PhysicalPartition] | None = None,
    changes: dict[str, PartitionChange] | None = None,
) -> PartitionedTablePlan:
    """Build one partitioned table plan for tests."""
    return PartitionedTablePlan(
        table_signature="sig",
        full_rebuild=full_rebuild,
        current_partitions=current or {},
        changes=changes or {},
    )


def add_change(
    partition_id: str, path: str = "", previous: PhysicalPartition | None = None
) -> PartitionChange:
    """Build one add partition change for tests."""
    return PartitionChange(
        kind="add",
        partition_id=partition_id,
        path=path,
        current=partition(partition_id),
        previous=previous,
    )


def remove_change(partition_id: str, previous: PhysicalPartition) -> PartitionChange:
    """Build one remove partition change for tests."""
    return PartitionChange(
        kind="remove",
        partition_id=partition_id,
        previous=previous,
    )


class TestPlannedPaths:
    """planned_paths behavior tests."""

    def test_returns_partition_paths_for_partitioned_table(self) -> None:
        """Return add/update change paths for a partitioned table."""
        plan = sync_plan(
            partitioned_tables={
                "p.d.t": table_plan(
                    current={"1": partition("1")},
                    changes={"1": add_change("1", "s3://b/1.parquet")},
                )
            }
        )
        assert planned_paths(plan, "p.d.t", plan.partitioned_tables["p.d.t"]) == [
            "s3://b/1.parquet"
        ]

    def test_returns_plan_paths_for_full_table(self) -> None:
        """Return plan paths for a full table."""
        plan = sync_plan(
            signatures={"p.d.t": "sig"},
            paths={"p.d.t": ["s3://b/t.parquet"]},
        )
        assert planned_paths(plan, "p.d.t", None) == ["s3://b/t.parquet"]

    def test_excludes_remove_changes_from_paths(self) -> None:
        """Exclude remove changes from the planned paths."""
        previous = partition("1")
        plan = sync_plan(
            partitioned_tables={
                "p.d.t": table_plan(
                    changes={"1": remove_change("1", previous)},
                )
            }
        )
        assert planned_paths(plan, "p.d.t", plan.partitioned_tables["p.d.t"]) == []


class TestEmptyIncrementalTables:
    """empty_incremental_tables behavior tests."""

    def test_returns_incremental_tables_with_no_changes(self) -> None:
        """Return incremental tables that have no changes and no full rebuild."""
        plan = sync_plan(
            partitioned_tables={
                "p.d.empty": table_plan(full_rebuild=False, changes={}),
                "p.d.active": table_plan(
                    full_rebuild=False,
                    current={"1": partition("1")},
                    changes={"1": add_change("1")},
                ),
                "p.d.rebuild": table_plan(full_rebuild=True, changes={}),
            }
        )
        assert empty_incremental_tables(plan) == {"p.d.empty"}


class TestFailedPartitionIds:
    """failed_partition_ids behavior tests."""

    def test_returns_only_add_update_partitions_with_failed_paths(self) -> None:
        """Return only add/update partitions whose path is in failed_paths."""
        plan = table_plan(
            current={"1": partition("1"), "2": partition("2")},
            changes={
                "1": add_change("1", "s3://b/1.parquet"),
                "2": add_change("2", "s3://b/2.parquet"),
            },
        )
        result = failed_partition_ids(plan, {"s3://b/1.parquet"})
        assert result == {"1"}

    def test_excludes_remove_changes(self) -> None:
        """Exclude remove changes from failed partition detection."""
        previous = partition("1")
        plan = table_plan(
            changes={
                "1": remove_change("1", previous),
            },
        )
        assert failed_partition_ids(plan, {"s3://b/1.parquet"}) == set()


class TestRestorePartition:
    """restore_partition behavior tests."""

    def test_restores_previous_partition(self) -> None:
        """Roll back to the previous partition state."""
        previous = partition("1", "old")
        current = partition("1", "new")
        plan = table_plan(
            current={"1": current},
            changes={"1": add_change("1", "s3://b/1.parquet", previous=previous)},
        )
        restore_partition(plan, "1")
        assert "1" not in plan.changes
        assert plan.current_partitions["1"] == previous

    def test_removes_new_partition_with_no_previous(self) -> None:
        """Remove a newly added partition that has no previous state."""
        current = partition("1")
        plan = table_plan(
            current={"1": current},
            changes={"1": add_change("1", "s3://b/1.parquet")},
        )
        restore_partition(plan, "1")
        assert "1" not in plan.changes
        assert "1" not in plan.current_partitions


class TestPlanPublication:
    """plan_publication behavior tests."""

    def test_blocks_full_rebuild_table_when_path_fails(self) -> None:
        """Block a full-rebuild table when any of its paths fail."""
        plan = sync_plan(
            signatures={"p.d.t": "sig"},
            paths={"p.d.t": ["s3://b/1.parquet", "s3://b/2.parquet"]},
        )
        _reduced, blocked, failed = plan_publication(plan, {"s3://b/1.parquet"})
        assert blocked == {"p.d.t"}
        assert failed == {}

    def test_restores_incremental_partitions_on_failure(self) -> None:
        """Restore failed incremental partitions instead of blocking."""
        previous = partition("1", "old")
        current = partition("1", "new")
        plan = sync_plan(
            partitioned_tables={
                "p.d.t": table_plan(
                    full_rebuild=False,
                    current={"1": current},
                    changes={
                        "1": add_change("1", "s3://b/1.parquet", previous=previous),
                    },
                )
            }
        )
        reduced, blocked, failed = plan_publication(plan, {"s3://b/1.parquet"})
        assert blocked == set()
        assert failed == {"p.d.t": {"1"}}
        assert reduced.partitioned_tables["p.d.t"].current_partitions["1"] == previous
        assert "1" not in reduced.partitioned_tables["p.d.t"].changes

    def test_blocks_full_rebuild_partitioned_table_on_failure(self) -> None:
        """Block a full-rebuild partitioned table when a partition fails."""
        current = partition("1")
        plan = sync_plan(
            partitioned_tables={
                "p.d.t": table_plan(
                    full_rebuild=True,
                    current={"1": current},
                    changes={"1": add_change("1", "s3://b/1.parquet")},
                )
            }
        )
        _reduced, blocked, failed = plan_publication(plan, {"s3://b/1.parquet"})
        assert blocked == {"p.d.t"}
        assert failed == {"p.d.t": {"1"}}

    def test_passes_through_unchanged_when_no_failures(self) -> None:
        """Pass through unchanged when no paths fail."""
        plan = sync_plan(
            signatures={"p.d.full": "sig"},
            paths={"p.d.full": ["s3://b/full.parquet"]},
            partitioned_tables={
                "p.d.part": table_plan(
                    current={"1": partition("1")},
                    changes={"1": add_change("1", "s3://b/1.parquet")},
                )
            },
        )
        reduced, blocked, failed = plan_publication(plan, set())
        assert blocked == set()
        assert failed == {}
        assert reduced.signatures == plan.signatures
        assert "1" in reduced.partitioned_tables["p.d.part"].changes
