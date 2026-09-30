"""Behavior tests for persisted table state."""

from data_proxy.models import (
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PublicationResult,
    Strategy,
    SyncPlan,
    TableState,
)
from data_proxy.state import build_table_states
from tests.helpers import partition, sync_config


class TestBuildTableStates:
    """Only published tables get a new persisted state."""

    def test_keeps_only_published_full_and_partitioned_tables(self) -> None:
        """Build state from the plan for published tables and skip the others."""
        partitions = {"1": partition("1")}
        plan = PartitionedTablePlan(
            table_signature="part-sig",
            full_rebuild=False,
            current_partitions=partitions,
            changes={},
        )
        config = sync_config(
            [
                FullTable(name="p.d.full"),
                FullTable(name="p.d.unpublished_full"),
                PartitionedTable(name="p.d.part"),
                PartitionedTable(name="p.d.unpublished_part"),
            ]
        )
        result = PublicationResult(
            plan=SyncPlan(
                schema_name="app",
                signatures={"p.d.full": "full-sig", "p.d.unpublished_full": "x"},
                paths={"p.d.full": ["s3://b/f"], "p.d.unpublished_full": ["s3://b/u"]},
                partitioned_tables={"p.d.part": plan, "p.d.unpublished_part": plan},
            ),
            published_tables={"p.d.full", "p.d.part"},
        )

        assert build_table_states(result, config) == {
            "p.d.full": TableState(strategy=Strategy.FULL, signature="full-sig"),
            "p.d.part": TableState(
                strategy=Strategy.PARTITIONED,
                signature="part-sig",
                partitions=partitions,
            ),
        }
