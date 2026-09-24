"""Tests for planning result types."""

from typing import Literal

import hypothesis
from hypothesis import given
from hypothesis import strategies as st

from data_proxy.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionManifest,
    SchemaConfig,
    SyncConfig,
    SyncWork,
    TableConfig,
    UnitMapping,
)
from data_proxy.planning import (
    build_partition_tasks,
    find_partition_changes,
    group_schema_plans,
    order_partition_ids,
    table_signature,
)
from tests.helpers import planning_partition


class TestSyncWork:
    """SyncWork behavior tests."""

    def test_starts_with_empty_plans_and_tasks(self) -> None:
        """Start with empty plans and tasks."""
        work = SyncWork(plans=[], tasks=[])
        assert work.plans == []
        assert work.tasks == []


class TestPartitionChanges:
    """PartitionChanges behavior tests."""

    @given(
        scenario=st.sampled_from(["new", "unchanged", "rebuild", "removed"]),
        partition_id=st.integers(1, 100).map(str),
    )
    def test_detects_partition_changes(
        self,
        scenario: Literal["new", "unchanged", "rebuild", "removed"],
        partition_id: str,
    ) -> None:
        """Detect new, unchanged, rebuilt, and removed partitions."""
        current = {partition_id: planning_partition(partition_id, "new")}
        expected: tuple[bool, set[str], set[str]]
        if scenario == "new":
            stored = None
            expected = (True, {partition_id}, set())
        elif scenario == "unchanged":
            stored = PartitionManifest(
                table_signature="s",
                partitions={partition_id: planning_partition(partition_id, "new")},
            )
            expected = (False, set(), set())
        elif scenario == "rebuild":
            stored = PartitionManifest(
                table_signature="old",
                partitions={partition_id: planning_partition(partition_id)},
            )
            expected = (True, {partition_id}, set())
        else:
            removed_id = str(int(partition_id) + 1)
            stored = PartitionManifest(
                table_signature="s",
                partitions={removed_id: planning_partition(removed_id)},
            )
            expected = (False, {partition_id}, {removed_id})
        result = find_partition_changes(current, stored, "s")
        assert (result.full_rebuild, result.changed, result.removed) == expected

    def test_ignores_logical_bytes_when_signature_unchanged(self) -> None:
        """Ignore logical-byte changes when the signature is unchanged."""
        stored = PartitionManifest(
            table_signature="s", partitions={"1": planning_partition("1", "sig")}
        )
        result = find_partition_changes(
            {"1": planning_partition("1", "sig", logical_bytes=999)}, stored, "s"
        )
        assert (result.full_rebuild, result.changed) == (False, set())


class TestPartitionBatching:
    """PartitionBatching behavior tests."""

    def test_orders_numeric_partitions_before_remainder(self) -> None:
        """Order numeric partitions before the remainder."""
        table = PartitionedTable(name="p.d.t")
        current = {
            "10": planning_partition("10"),
            "2": planning_partition("2"),
            "__NULL__": planning_partition("__NULL__"),
        }
        batch = build_partition_tasks(table, current, set(current), "run", "bucket", [])
        assert list(batch.paths) == ["2", "10", "__NULL__"]

    def test_builds_paths_and_tasks_for_changed_partitions(self) -> None:
        """Build paths and tasks for changed partitions."""
        table = PartitionedTable(name="p.d.t", resolved_schema="app")
        current = {
            partition_id: planning_partition(partition_id, logical_bytes=300)
            for partition_id in ("1", "2", "3", "4")
        }
        batch = build_partition_tasks(table, current, set(current), "run", "bucket", [])
        assert batch.paths == {
            "1": "s3://bucket/tmp/app/t/batches/0/data-0.parquet",
            "2": "s3://bucket/tmp/app/t/batches/0/data-1.parquet",
            "3": "s3://bucket/tmp/app/t/batches/0/data-2.parquet",
            "4": "s3://bucket/tmp/app/t/batches/0/data-3.parquet",
        }
        assert [len(task.selections) for task in batch.tasks] == [4]


class TestTableSignature:
    """TableSignature behavior tests."""

    @given(
        case=st.sampled_from(
            [
                (
                    FullTable(
                        name="p.d.t",
                        rls=[UnitMapping(column="id", unit_type="unit")],
                    ),
                    None,
                    FullTable(
                        name="p.d.t",
                        rls=[UnitMapping(column="id", unit_type="cras")],
                    ),
                    None,
                    True,
                ),
                (
                    FullTable(
                        name="p.d.t", indexes=[IndexConfig(name="idx", columns=["col"])]
                    ),
                    None,
                    FullTable(
                        name="p.d.t",
                        indexes=[IndexConfig(name="idx", columns=["other"])],
                    ),
                    None,
                    True,
                ),
                (
                    FullTable(name="p.d.t"),
                    "old_claim",
                    FullTable(name="p.d.t"),
                    "new_claim",
                    True,
                ),
                (
                    FullTable(name="p.d.t", resolved_schema="schema_x"),
                    None,
                    FullTable(name="p.d.t", resolved_schema="schema_y"),
                    None,
                    False,
                ),
            ]
        )
    )
    def test_changes_signature_for_relevant_fields(
        self,
        case: tuple[TableConfig, str | None, TableConfig, str | None, bool],
    ) -> None:
        """Change the signature when relevant fields change."""
        table_a, claim_a, table_b, claim_b, should_differ = case
        sig_a = table_signature(table_a, claim_a, "m")
        sig_b = table_signature(table_b, claim_b, "m")
        assert (sig_a != sig_b) == should_differ

    @hypothesis.given(
        schema_x=st.text(
            min_size=1, alphabet=st.characters(whitelist_categories=("Ll",))
        ),
        schema_y=st.text(
            min_size=1, alphabet=st.characters(whitelist_categories=("Ll",))
        ),
    )
    @hypothesis.example(schema_x="app", schema_y="other")
    def test_ignores_resolved_schema_in_signature(
        self, schema_x: str, schema_y: str
    ) -> None:
        """Ignore resolved schema in the signature."""
        table_x = FullTable(name="p.d.t", resolved_schema=schema_x)
        table_y = FullTable(name="p.d.t", resolved_schema=schema_y)
        assert table_signature(table_x, None, "m") == table_signature(
            table_y, None, "m"
        )


class TestPartitionOrdering:
    """Partition ordering behavior tests."""

    @given(
        partition_ids=st.lists(
            st.integers(0, 10_000).map(str), min_size=0, max_size=12, unique=True
        ),
        include_remainder=st.booleans(),
    )
    def test_orders_numeric_ids_before_remainder(
        self, partition_ids: list[str], include_remainder: bool
    ) -> None:
        """Order numeric IDs ascending and place the remainder last."""
        changed = set(partition_ids)
        if include_remainder:
            changed.add("__NULL__")
        ordered = order_partition_ids(changed)
        numeric = [value for value in ordered if value != "__NULL__"]
        assert numeric == sorted(numeric, key=int)
        if "__NULL__" in changed:
            assert ordered[-1] == "__NULL__"


class TestSchemaPlanGrouping:
    """Schema plan grouping behavior tests."""

    @given(first=st.booleans(), second=st.booleans())
    def test_groups_full_table_plans_by_resolved_schema(
        self, first: bool, second: bool
    ) -> None:
        """Group full-table plans under their resolved schemas."""
        tables = [
            FullTable(name="p.one.first", resolved_schema="one"),
            FullTable(name="p.two.second", resolved_schema="two"),
        ]
        selected = [
            table
            for table, enabled in zip(tables, [first, second], strict=True)
            if enabled
        ]
        config = SyncConfig(
            schemas={
                "one": SchemaConfig(tables=[tables[0]]),
                "two": SchemaConfig(tables=[tables[1]]),
            }
        )
        signatures = {table.name: "signature" for table in selected}
        paths = {table.name: [f"s3://bucket/{table.table_name}"] for table in selected}
        plans = group_schema_plans(config, signatures, paths, {})
        grouped = {plan.schema_name: set(plan.signatures) for plan in plans}
        expected = {table.resolved_schema: {table.name} for table in selected}
        assert grouped == expected
