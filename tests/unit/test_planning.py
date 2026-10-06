"""Tests for planning result types."""

from unittest.mock import AsyncMock, MagicMock

import hypothesis
import pytest
from hypothesis import given
from hypothesis import strategies as st

import data_proxy.planning as planning
import data_proxy.sources.registry as source_registry
from data_proxy.duckdb import DuckDB
from data_proxy.models import (
    DuckLakeTableConfig,
    FullTable,
    PartitionChange,
    PartitionedTable,
    SchemaConfig,
    Strategy,
    SyncConfig,
    TableConfig,
    TableState,
    UnitMapping,
)
from data_proxy.planning import (
    build_partition_tasks,
    configured_source,
    expand_config,
    find_partition_changes,
    group_schema_plans,
    table_signature,
)
from data_proxy.postgres import Postgres
from data_proxy.sources.partitions import order_partition_ids
from data_proxy.sources.source import Source
from tests.fixtures.types import FullOnlySource
from tests.helpers import partition


class TestPartitionChanges:
    """PartitionChanges behavior tests."""

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            pytest.param(None, {"1": "add"}, id="first-sync-adds"),
            pytest.param(
                TableState(
                    strategy=Strategy.PARTITIONED,
                    signature="s",
                    partitions={"1": partition("1", signature="new")},
                ),
                {},
                id="unchanged-partition-skipped",
            ),
            pytest.param(
                TableState(
                    strategy=Strategy.PARTITIONED,
                    signature="old",
                    partitions={"1": partition("1", signature="new")},
                ),
                {"1": "update"},
                id="table-signature-change-rebuilds",
            ),
            pytest.param(
                TableState(
                    strategy=Strategy.PARTITIONED,
                    signature="s",
                    partitions={"1": partition("1", signature="old")},
                ),
                {"1": "update"},
                id="partition-signature-change-updates",
            ),
            pytest.param(
                TableState(
                    strategy=Strategy.PARTITIONED,
                    signature="s",
                    partitions={"2": partition("2")},
                ),
                {"1": "add", "2": "remove"},
                id="new-partition-added-and-missing-removed",
            ),
        ],
    )
    def test_detects_partition_changes(
        self, stored: TableState | None, expected: dict[str, str]
    ) -> None:
        """Detect added, updated, removed, and unchanged partitions."""
        current = {"1": partition("1", signature="new")}

        result = find_partition_changes(current, stored, "s")

        assert {pid: change.kind for pid, change in result.items()} == expected

    def test_ignores_logical_bytes_when_signature_unchanged(self) -> None:
        """Ignore logical-byte changes when the signature is unchanged."""
        stored = TableState(
            strategy=Strategy.FULL,
            signature="s",
            partitions={"1": partition("1", signature="sig")},
        )
        result = find_partition_changes(
            {"1": partition("1", signature="sig", logical_bytes=999)}, stored, "s"
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
        previous = partition("1", signature="old")
        stored = TableState(
            strategy=Strategy.FULL,
            signature="signature",
            partitions={"1": previous},
        )
        source = MagicMock()
        source.partitions = AsyncMock(return_value=("signature", {}))
        read_manifest = AsyncMock(return_value=stored)
        discover_columns = AsyncMock(return_value=[])
        monkeypatch.setattr(planning, "read_table_state", read_manifest)
        monkeypatch.setattr(planning, "discover_json_columns", discover_columns)

        plan, tasks = await planning.plan_partitioned_table(
            source,
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


class TestChangedPartitionPlanning:
    """Partition planning behavior when source partitions are added or updated."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stored", "full_rebuild"),
        [
            pytest.param(None, True, id="first-sync"),
            pytest.param(
                TableState(
                    strategy=Strategy.PARTITIONED,
                    signature="signature",
                    partitions={"1": partition("1", signature="old")},
                ),
                False,
                id="partition-updated",
            ),
            pytest.param(
                TableState(
                    strategy=Strategy.PARTITIONED,
                    signature="old-signature",
                    partitions={"1": partition("1", signature="new")},
                ),
                True,
                id="table-signature-changed",
            ),
        ],
    )
    async def test_plans_an_extraction_task_for_changed_partitions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        stored: TableState | None,
        full_rebuild: bool,
    ) -> None:
        """Extract changed partitions with the discovered JSON columns."""
        table = PartitionedTable(name="p.d.t", resolved_schema="app")
        current = {"1": partition("1", signature="new")}
        source = MagicMock()
        source.partitions = AsyncMock(return_value=("signature", current))
        monkeypatch.setattr(
            planning, "read_table_state", AsyncMock(return_value=stored)
        )
        monkeypatch.setattr(
            planning, "discover_json_columns", AsyncMock(return_value=["payload"])
        )

        plan, tasks = await planning.plan_partitioned_table(
            source,
            AsyncMock(spec=Postgres),
            AsyncMock(spec=DuckDB),
            table,
            "run",
            "bucket",
        )

        assert plan is not None
        assert plan.full_rebuild is full_rebuild
        assert plan.current_partitions == current
        assert [task.selections for task in tasks] == [[current["1"].selection]]
        assert tasks[0].json_columns == ["payload"]
        assert plan.changes["1"].path == tasks[0].output_paths[0]


class TestDetectChanges:
    """Full-table change detection against stored signatures."""

    @pytest.mark.asyncio
    async def test_returns_only_full_tables_with_a_new_signature(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Skip unchanged and partitioned tables."""
        unchanged = FullTable(name="p.d.unchanged", resolved_schema="app")
        changed = FullTable(name="p.d.changed", resolved_schema="app")
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(
                    tables=[unchanged, changed, PartitionedTable(name="p.d.part")]
                )
            }
        )
        source = MagicMock()
        source.modified = AsyncMock(return_value="modified")
        source.close = AsyncMock()
        configure_source = MagicMock(return_value=source)
        monkeypatch.setattr(source_registry.sources, "configure", configure_source)
        monkeypatch.setattr(
            planning,
            "read_table_signature",
            AsyncMock(
                side_effect=[
                    table_signature(unchanged, None, "modified"),
                    "old-signature",
                ]
            ),
        )

        result = await planning.detect_changes(AsyncMock(spec=Postgres), config)

        assert result == {changed.name: table_signature(changed, None, "modified")}
        assert [call.args[0] for call in source.modified.await_args_list] == [
            unchanged.name,
            changed.name,
        ]


class TestConfiguredSources:
    """Configured source lifetime and scope behavior."""

    @pytest.mark.asyncio
    async def test_closes_sources_after_full_table_expansion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Close a configured source after its schema discovery completes."""
        source = MagicMock()
        source.close = AsyncMock()
        monkeypatch.setattr(
            source_registry.sources, "configure", MagicMock(return_value=source)
        )
        monkeypatch.setattr(
            planning, "discover_json_columns", AsyncMock(return_value=[])
        )

        await expand_config(
            AsyncMock(spec=DuckDB),
            [FullTable(name="p.d.one", resolved_schema="app")],
            "bucket",
            "run",
        )

        source.close.assert_awaited_once()

    def test_scopes_cached_sources_to_the_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Do not reuse one source type across schema-specific settings."""
        first = MagicMock()
        second = MagicMock()
        configure = MagicMock(side_effect=[first, second])
        monkeypatch.setattr(source_registry.sources, "configure", configure)
        active: dict[str, Source] = {}
        one = FullTable(
            name="p.d.one",
            resolved_schema="one",
            resolved_source="example",
            resolved_source_settings={"location": "one"},
        )
        two = FullTable(
            name="p.d.two",
            resolved_schema="two",
            resolved_source="example",
            resolved_source_settings={"location": "two"},
        )

        assert configured_source(one, active) is first
        assert configured_source(two, active) is second
        assert configure.call_count == 2


class TestSourceCapabilities:
    """Source strategy compatibility tests."""

    @pytest.mark.asyncio
    async def test_rejects_partitioned_tables_for_a_full_only_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail before querying an source that lacks partition support."""
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[PartitionedTable(name="p.d.events")])}
        )
        monkeypatch.setattr(
            source_registry.sources,
            "configure",
            MagicMock(return_value=FullOnlySource()),
        )

        with pytest.raises(TypeError, match="does not support partitioned tables"):
            await planning.plan_partitioned_tables(
                AsyncMock(spec=Postgres),
                AsyncMock(spec=DuckDB),
                config,
                "run",
                "bucket",
            )


class TestPartitionBatching:
    """PartitionBatching behavior tests."""

    def test_builds_paths_and_tasks_for_changed_partitions(self) -> None:
        """Build paths and tasks for changed partitions."""
        table = PartitionedTable(name="p.d.t", resolved_schema="app")
        current = {
            partition_id: partition(partition_id, logical_bytes=300)
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

    @pytest.mark.parametrize(
        ("before", "claim_before", "after", "claim_after"),
        [
            pytest.param(
                FullTable(
                    name="p.d.t", rls=[UnitMapping(column="id", unit_type="unit")]
                ),
                None,
                FullTable(
                    name="p.d.t", rls=[UnitMapping(column="id", unit_type="region")]
                ),
                None,
                id="rls",
            ),
            pytest.param(
                FullTable(name="p.d.t", ducklake=DuckLakeTableConfig(sort=["col"])),
                None,
                FullTable(name="p.d.t", ducklake=DuckLakeTableConfig(sort=["other"])),
                None,
                id="sort",
            ),
            pytest.param(
                FullTable(name="p.d.t"),
                "old_claim",
                FullTable(name="p.d.t"),
                "new_claim",
                id="claim",
            ),
            pytest.param(
                PartitionedTable(name="p.d.t", n=4),
                None,
                PartitionedTable(name="p.d.t", n=5),
                None,
                id="partition-window",
            ),
            pytest.param(
                FullTable(name="p.d.t"),
                None,
                PartitionedTable(name="p.d.t"),
                None,
                id="strategy",
            ),
        ],
    )
    def test_changes_signature_for_relevant_fields(
        self,
        before: TableConfig,
        claim_before: str | None,
        after: TableConfig,
        claim_after: str | None,
    ) -> None:
        """Change the signature when a field that affects the output changes."""
        assert table_signature(before, claim_before, "m") != table_signature(
            after, claim_after, "m"
        )

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
