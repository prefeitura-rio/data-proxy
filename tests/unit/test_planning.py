"""Tests for planning result types."""

from typing import Literal
from unittest.mock import AsyncMock

import hypothesis
import pytest
from hypothesis import given
from hypothesis import strategies as st

import data_proxy.planning as planning
from data_proxy.bigquery.clients import BigQuery
from data_proxy.duckdb import DuckDB
from data_proxy.models import (
    DuckLakeTableConfig,
    FullTable,
    PartitionChange,
    PartitionedTable,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncWork,
    TableConfig,
    TableState,
    UnitMapping,
)
from data_proxy.planning import (
    build_partition_tasks,
    find_partition_changes,
    group_schema_plans,
    order_partition_ids,
    partition_sort_key,
    table_signature,
)
from data_proxy.postgres import Postgres
from tests.helpers import planning_partition


class TestPlanningContext:
    """Planning context behavior tests."""

    def test_returns_empty_work_when_no_changes_exist(self) -> None:
        """Do not create plans or tasks when nothing changed."""
        context = planning.PlanningContext(
            pg_conn=AsyncMock(spec=Postgres),
            duckdb_conn=AsyncMock(spec=DuckDB),
            config=SyncConfig(schemas={}),
            sync_id="run",
            bucket="bucket",
        )

        assert context.group() == SyncWork(plans=[], tasks=[])


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
        if scenario == "new":
            stored = None
            expected_kinds: dict[str, str] = {partition_id: "add"}
            expected_ids: set[str] = {partition_id}
        elif scenario == "unchanged":
            stored = TableState(
                strategy=Strategy.FULL,
                signature="s",
                partitions={partition_id: planning_partition(partition_id, "new")},
            )
            expected_kinds = {}
            expected_ids = set()
        elif scenario == "rebuild":
            stored = TableState(
                strategy=Strategy.FULL,
                signature="old",
                partitions={partition_id: planning_partition(partition_id)},
            )
            expected_kinds = {partition_id: "update"}
            expected_ids = {partition_id}
        else:
            removed_id = str(int(partition_id) + 1)
            stored = TableState(
                strategy=Strategy.FULL,
                signature="s",
                partitions={removed_id: planning_partition(removed_id)},
            )
            expected_kinds = {partition_id: "add", removed_id: "remove"}
            expected_ids = {partition_id, removed_id}

        result = find_partition_changes(current, stored, "s")
        assert set(result) == expected_ids
        assert {pid: c.kind for pid, c in result.items()} == expected_kinds

    def test_ignores_logical_bytes_when_signature_unchanged(self) -> None:
        """Ignore logical-byte changes when the signature is unchanged."""
        stored = TableState(
            strategy=Strategy.FULL,
            signature="s",
            partitions={"1": planning_partition("1", "sig")},
        )
        result = find_partition_changes(
            {"1": planning_partition("1", "sig", logical_bytes=999)}, stored, "s"
        )
        assert result == {}


class TestRemovalOnlyPartitionPlanning:
    """Partition planning behavior when source partitions disappear."""

    @pytest.mark.asyncio
    async def test_plans_removed_partitions_without_extracting_empty_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return a deletion plan and no extraction tasks when all rows disappear."""
        table = PartitionedTable(name="p.d.t", resolved_schema="app")
        previous = planning_partition("1", "old")
        stored = TableState(
            strategy=Strategy.FULL,
            signature="signature",
            partitions={"1": previous},
        )
        physical = AsyncMock(return_value=("signature", {}))
        read_manifest = AsyncMock(return_value=stored)
        discover_columns = AsyncMock(return_value=[])
        monkeypatch.setattr(planning, "physical_partitions", physical)
        monkeypatch.setattr(planning, "read_table_state", read_manifest)
        monkeypatch.setattr(planning, "discover_json_columns", discover_columns)

        plan, tasks = await planning.plan_partitioned_table(
            AsyncMock(spec=BigQuery),
            AsyncMock(spec=Postgres),
            AsyncMock(spec=DuckDB),
            table,
            "run",
            "bucket",
        )

        assert plan is not None
        assert plan.changes == {
            "1": PartitionChange(
                kind="remove",
                partition_id="1",
                previous=previous,
            )
        }
        assert tasks == []
        discover_columns.assert_not_awaited()


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
        changes = find_partition_changes(current, None, "s")
        changes, _ = build_partition_tasks(table, current, changes, "run", "bucket", [])
        ordered_adds = sorted(
            (c.partition_id for c in changes.values() if c.kind == "add"),
            key=partition_sort_key,
        )
        assert ordered_adds == ["2", "10", "__NULL__"]
        assert all(c.path for c in changes.values())

    def test_builds_paths_and_tasks_for_changed_partitions(self) -> None:
        """Build paths and tasks for changed partitions."""
        table = PartitionedTable(name="p.d.t", resolved_schema="app")
        current = {
            partition_id: planning_partition(partition_id, logical_bytes=300)
            for partition_id in ("1", "2", "3", "4")
        }
        changes = find_partition_changes(current, None, "s")
        changes, task = build_partition_tasks(
            table, current, changes, "run", "bucket", []
        )
        assert {pid: change.path for pid, change in changes.items()} == {
            "1": "s3://bucket/tmp/app/t/batches/0/data-0.parquet",
            "2": "s3://bucket/tmp/app/t/batches/0/data-1.parquet",
            "3": "s3://bucket/tmp/app/t/batches/0/data-2.parquet",
            "4": "s3://bucket/tmp/app/t/batches/0/data-3.parquet",
        }
        assert len(task.selections) == 4


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
                        rls=[UnitMapping(column="id", unit_type="region")],
                    ),
                    None,
                    True,
                ),
                (
                    FullTable(name="p.d.t", ducklake=DuckLakeTableConfig(sort=["col"])),
                    None,
                    FullTable(
                        name="p.d.t",
                        ducklake=DuckLakeTableConfig(sort=["other"]),
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
