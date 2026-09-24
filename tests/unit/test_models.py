"""Unit tests for synchronization data models."""

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from data_proxy.models import (
    AllSelection,
    DumpFailure,
    DumpSuccess,
    DumpTask,
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
    TableConfig,
    TimeRangeSelection,
    UnitMapping,
)
from tests.helpers import partition
from tests.strategies import (
    identifiers,
    physical_partitions,
    table_configs,
)


class TestSelectionValidation:
    """Selection validation behavior tests."""

    @given(lower=st.integers(-100, 100), width=st.integers(0, 100))
    def test_rejects_empty_or_reversed_range(self, lower: int, width: int) -> None:
        """Reject an empty or reversed integer range."""
        with pytest.raises(ValidationError):
            RangeSelection(
                partition_id="1", column="id", lower=lower, upper=lower - width
            )

    @given(
        lower=st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 1, 1)),
        width=st.integers(0, 30),
    )
    def test_rejects_empty_or_reversed_time_range(
        self, lower: date, width: int
    ) -> None:
        """Reject an empty or reversed time range."""
        upper = lower - timedelta(days=width)
        with pytest.raises(ValidationError):
            TimeRangeSelection(
                column="created_at", lower=lower.isoformat(), upper=upper.isoformat()
            )

    @given(partition=physical_partitions(), other_id=identifiers)
    def test_rejects_mismatched_range_partition_id(
        self, partition: PhysicalPartition, other_id: str
    ) -> None:
        """Reject a range selection with a different partition ID."""
        if other_id == partition.partition_id:
            return
        with pytest.raises(ValidationError):
            PhysicalPartition(
                partition_id=other_id,
                signature=partition.signature,
                selection=partition.selection,
                logical_bytes=partition.logical_bytes,
            )


class TestTableConfiguration:
    """Table configuration behavior tests."""

    def test_returns_source_table_name(self) -> None:
        """Return the unqualified source table name."""
        assert FullTable(name="p.dataset.people").table_name == "people"

    def test_includes_full_table_signature_fields(self) -> None:
        """Include full-table strategy fields in the signature configuration."""
        fields = FullTable(name="p.d.t").config_signature_fields()
        assert fields["strategy"] == "full"
        assert fields["n"] is None

    @given(n=st.integers(1, 100))
    def test_includes_partition_window_in_partitioned_signature(self, n: int) -> None:
        """Include the partition window in a partitioned-table signature."""
        fields = PartitionedTable(name="p.d.t", n=n).config_signature_fields()
        assert fields["strategy"] == "partitioned"
        assert fields["n"] == n

    @given(table=table_configs())
    def test_returns_unqualified_table_name(self, table: TableConfig) -> None:
        """Return the final component of a table reference."""
        assert table.table_name == table.name.split(".")[-1]

    @given(table=table_configs(), run_id=identifiers, bucket=identifiers)
    def test_builds_task_path_from_table_configuration(
        self, table: TableConfig, run_id: str, bucket: str
    ) -> None:
        """Build an extraction path from table configuration."""
        task = table.to_task(run_id, bucket, "tmp", [AllSelection()])
        scratch_prefix = f"s3://{bucket}/tmp/"  # noqa: S108
        assert task.bucket_path == (
            scratch_prefix + f"{table.resolved_schema}/{table.table_name}/data.parquet"
        )


class TestSchemaConfiguration:
    """Schema configuration behavior tests."""

    @given(
        schema=identifiers, table=st.from_regex("p\\.[a-z]+\\.[a-z]+", fullmatch=True)
    )
    def test_stamps_table_schema_from_config_key(self, schema: str, table: str) -> None:
        """Stamp each table with its containing schema."""
        config = SyncConfig(
            schemas={schema: SchemaConfig(tables=[FullTable(name=table)])}
        )
        assert config.tables[0].resolved_schema == schema

    def test_accepts_unique_table_names(self) -> None:
        """Accept one configured source table name."""
        config = SyncConfig(
            schemas={"one": SchemaConfig(tables=[FullTable(name="p.d.t")])}
        )
        assert [table.name for table in config.tables] == ["p.d.t"]

    def test_rejects_duplicate_table_names(self) -> None:
        """Reject the same source table in two schemas."""
        with pytest.raises(ValueError, match="Duplicate"):
            SyncConfig(
                schemas={
                    "one": SchemaConfig(tables=[FullTable(name="p.d.t")]),
                    "two": SchemaConfig(tables=[FullTable(name="p.d.t")]),
                }
            )

    def test_accepts_non_reserved_table_name(self) -> None:
        """Accept a source table with a non-reserved name."""
        assert (
            SyncConfig(
                schemas={"one": SchemaConfig(tables=[FullTable(name="p.d.people")])}
            )
            .tables[0]
            .table_name
            == "people"
        )

    def test_rejects_reserved_freshness_table(self) -> None:
        """Reject a source table named freshness."""
        with pytest.raises(ValueError, match="reserved"):
            SyncConfig(
                schemas={"one": SchemaConfig(tables=[FullTable(name="p.d.freshness")])}
            )

    def test_rejects_rls_without_schema_claim(self) -> None:
        """Reject RLS tables without an identity claim."""
        with pytest.raises(ValueError, match="no claim"):
            SyncConfig(
                schemas={
                    "one": SchemaConfig(
                        tables=[
                            FullTable(
                                name="p.d.t",
                                rls=[UnitMapping(column="unit_id", unit_type="unit")],
                            )
                        ]
                    )
                }
            )


class TestTaskResults:
    """Task result behavior tests."""

    @given(run_id=identifiers, path=st.from_regex("s3://[a-z]+/[a-z]+", fullmatch=True))
    def test_task_id_is_deterministic(self, run_id: str, path: str) -> None:
        """Build the same task ID for the same run and path."""
        first = DumpTask(
            run_id=run_id,
            table="p.d.t",
            target_schema="d",
            bucket_path=path,
            selections=[AllSelection()],
        )
        second = first.model_copy(deep=True)
        assert first.task_id == second.task_id

    def test_success_has_no_failed_paths(self) -> None:
        """Return no failed paths for a successful task."""
        assert DumpSuccess().failed_paths == []

    @given(path=st.from_regex("s3://[a-z]+/[a-z]+", fullmatch=True))
    def test_failure_returns_failed_paths(self, path: str) -> None:
        """Return all failed paths for a failed task."""
        assert DumpFailure(failed_paths=[path]).failed_paths == [path]

    @given(run_id=identifiers, path=st.from_regex("s3://[a-z]+/[a-z]+", fullmatch=True))
    def test_changes_task_id_when_run_changes(self, run_id: str, path: str) -> None:
        """Change the task ID when the run ID changes."""
        first = DumpTask(
            run_id=run_id,
            table="p.d.t",
            target_schema="d",
            bucket_path=path,
            selections=[AllSelection()],
        )
        second = first.model_copy(update={"run_id": f"{run_id}x"})
        assert first.task_id != second.task_id

    @given(run_id=identifiers, path=st.from_regex("s3://[a-z]+/[a-z]+", fullmatch=True))
    def test_changes_task_id_when_path_changes(self, run_id: str, path: str) -> None:
        """Change the task ID when the bucket path changes."""
        first = DumpTask(
            run_id=run_id,
            table="p.d.t",
            target_schema="d",
            bucket_path=path,
            selections=[AllSelection()],
        )
        second = first.model_copy(update={"bucket_path": f"{path}/next"})
        assert first.task_id != second.task_id


class TestPlanValidation:
    """Synchronization plan validation behavior tests."""

    def test_rejects_changed_path_without_current_partition(self) -> None:
        """Reject a changed path absent from the current manifest."""
        with pytest.raises(ValueError, match="Changed partition paths"):
            PartitionedTablePlan(
                table_signature="s",
                full_rebuild=False,
                current_partitions={},
                changed_paths={"1": "path"},
                removed_partitions={},
            )

    def test_rejects_mismatched_sync_plan_paths(self) -> None:
        """Reject signatures and paths with different table keys."""
        with pytest.raises(ValueError, match="signatures"):
            SyncPlan(schema_name="d", signatures={"p.d.t": "s"}, paths={})

    def test_rejects_empty_sync_plan_path(self) -> None:
        """Reject a sync plan with an empty path list."""
        with pytest.raises(ValueError, match="non-empty paths"):
            SyncPlan(schema_name="d", signatures={"p.d.t": "s"}, paths={"p.d.t": []})

    def test_rejects_ordinary_and_partitioned_plan_for_one_table(self) -> None:
        """Reject ordinary and partitioned plans for the same table."""
        partitioned = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        with pytest.raises(ValueError, match="ordinary and partitioned"):
            SyncPlan(
                schema_name="d",
                signatures={"p.d.t": "s"},
                paths={"p.d.t": ["path"]},
                partitioned_tables={"p.d.t": partitioned},
            )

    def test_accepts_previous_partition_marked_changed(self) -> None:
        """Accept a previous partition that is listed as changed."""
        physical = partition("1")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"1": physical},
            changed_paths={"1": "path"},
            previous_partitions={"1": physical},
            removed_partitions={},
        )
        assert plan.previous_partitions == {"1": physical}

    def test_rejects_previous_partition_not_marked_changed(self) -> None:
        """Reject a previous partition that is absent from changed paths."""
        with pytest.raises(ValueError, match="Previous partitions"):
            PartitionedTablePlan(
                table_signature="s",
                full_rebuild=False,
                current_partitions={"1": partition("1")},
                changed_paths={},
                previous_partitions={"1": partition("1")},
                removed_partitions={},
            )

    def test_rejects_removed_partition_in_current_manifest(self) -> None:
        """Reject a partition marked as both current and removed."""
        physical = partition("0")
        with pytest.raises(ValueError, match="Removed partitions"):
            PartitionedTablePlan(
                table_signature="s",
                full_rebuild=False,
                current_partitions={"0": physical},
                changed_paths={},
                removed_partitions={"0": physical},
            )

    def test_reports_changed_publication_tables(self) -> None:
        """Report ordinary and partitioned tables with planned changes."""
        partitioned = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        plan = SyncPlan(
            schema_name="d",
            signatures={"p.d.full": "s"},
            paths={"p.d.full": ["path"]},
            partitioned_tables={"p.d.partitioned": partitioned},
        )
        assert (plan.signatures.keys() | plan.partitioned_tables.keys()) == {
            "p.d.full",
            "p.d.partitioned",
        }
