"""Unit tests for synchronization data models."""

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from data_proxy.models import (
    DuckLakePartition,
    DuckLakePartitionTransform,
    DuckLakeTableConfig,
    DumpTask,
    FullTable,
    PartitionChange,
    PartitionedTable,
    PartitionedTablePlan,
    SchemaConfig,
    SourceConfig,
    SyncConfig,
    SyncPlan,
    TableConfig,
    UnitMapping,
)
from data_proxy.sources.partitions import (
    AllSelection,
    PhysicalPartition,
    RangeSelection,
    TimeRangeSelection,
)
from data_proxy.sources.registry import sources
from tests.fixtures.types import NoFallbackSource
from tests.helpers import partition
from tests.strategies import (
    identifiers,
    physical_partitions,
    table_configs,
)


class TestSelectionValidation:
    """Selection validation behavior tests."""

    @pytest.mark.parametrize(
        "build",
        [
            pytest.param(
                lambda: RangeSelection(partition_id="", column="id", lower=0, upper=1),
                id="range-partition-id",
            ),
            pytest.param(
                lambda: RangeSelection(partition_id="1", column="", lower=0, upper=1),
                id="range-column",
            ),
            pytest.param(
                lambda: TimeRangeSelection(column="", lower="a", upper="b"),
                id="time-range-column",
            ),
            pytest.param(
                lambda: UnitMapping(column="unit_id", unit_type=""),
                id="unit-mapping-unit-type",
            ),
        ],
    )
    def test_rejects_an_empty_string_where_text_is_required(
        self, build: Callable[[], BaseModel]
    ) -> None:
        """Reject an empty required string in both the model and partition types."""
        with pytest.raises(ValidationError):
            build()

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

    def test_stores_partitioning_under_ducklake_settings(self) -> None:
        """Store custom partition transforms in the nested DuckLake key."""
        table = FullTable(
            name="p.d.events",
            ducklake=DuckLakeTableConfig(
                partitioning=[
                    DuckLakePartition(
                        column="event_time",
                        transform=DuckLakePartitionTransform.MONTH,
                    )
                ]
            ),
        )

        assert table.ducklake.partitioning is not None
        assert table.ducklake.partitioning[0].transform == "month"

    def test_rejects_fallback_on_a_full_table(self) -> None:
        """Allow fallback only for partitioned tables."""
        with pytest.raises(ValidationError, match="fallback"):
            FullTable.model_validate({"name": "p.d.events", "fallback": True})

    def test_includes_partition_fallback_in_the_signature(self) -> None:
        """Resync when partition fallback changes."""
        without_fallback = PartitionedTable(name="p.d.events")
        with_fallback = PartitionedTable(name="p.d.events", fallback=True)

        assert (
            without_fallback.config_signature_fields()
            != with_fallback.config_signature_fields()
        )

    def test_rejects_table_level_encryption(self) -> None:
        """Require encryption to be configured on the schema."""
        with pytest.raises(ValueError, match="schema scope"):
            FullTable(
                name="p.d.events",
                ducklake=DuckLakeTableConfig(encrypted=True),
            )

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
        task = config.tables[0].to_task("run", "bucket", "tmp", [AllSelection()])
        assert task.target_schema == schema
        assert task.source == "bigquery"

    def test_rejects_empty_schema_source_settings(self) -> None:
        """Require empty source settings to be omitted."""
        with pytest.raises(ValueError, match="must be omitted"):
            SourceConfig(type="bigquery", settings={})

    def test_rejects_partition_fallback_without_source_support(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject fallback when the configured source has no helper metadata."""
        monkeypatch.setitem(sources.sources, "no-fallback", NoFallbackSource())

        with pytest.raises(ValueError, match="does not support partition fallback"):
            SyncConfig(
                schemas={
                    "app": SchemaConfig(
                        source=SourceConfig(type="no-fallback"),
                        tables=[PartitionedTable(name="p.d.events", fallback=True)],
                    )
                }
            )

    def test_rejects_an_unknown_schema_source(self) -> None:
        """Reject a schema source absent from the ingestion registry."""
        with pytest.raises(ValueError, match="unknown source: missing"):
            SyncConfig(
                schemas={
                    "app": SchemaConfig(
                        source=SourceConfig(type="missing"),
                        tables=[FullTable(name="p.d.events")],
                    )
                }
            )

    def test_includes_the_resolved_source_in_a_table_signature(self) -> None:
        """Invalidate sync state when an adapter changes."""
        table = FullTable(name="p.d.events", resolved_source="bigquery")
        changed = table.model_copy(update={"resolved_source": "other"})

        assert table.config_signature_fields() != changed.config_signature_fields()

    def test_rejects_duplicate_destination_table_names(self) -> None:
        """Reject source tables that would replace the same destination view."""
        with pytest.raises(ValueError, match="Duplicate destination table names"):
            SyncConfig(
                schemas={
                    "app": SchemaConfig(
                        tables=[
                            FullTable(name="p.one.users"),
                            FullTable(name="p.two.users"),
                        ]
                    )
                }
            )

    def test_rejects_duplicate_table_names(self) -> None:
        """Reject the same source table in two schemas."""
        with pytest.raises(ValueError, match="Duplicate"):
            SyncConfig(
                schemas={
                    "one": SchemaConfig(tables=[FullTable(name="p.d.t")]),
                    "two": SchemaConfig(tables=[FullTable(name="p.d.t")]),
                }
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


class TestModelDefaults:
    """Pydantic model defaults are independent between instances."""

    def test_does_not_share_nested_models_or_collections(self) -> None:
        """Keep independent configuration and plan defaults."""
        first_config = SyncConfig()
        second_config = SyncConfig()
        first_plan = SyncPlan(schema_name="app")
        second_plan = SyncPlan(schema_name="app")

        first_config.schemas["app"] = SchemaConfig()
        first_config.schemas["app"].tables.append(FullTable(name="p.d.events"))
        first_plan.paths["p.d.events"] = ["s3://bucket/one"]

        assert second_config.schemas == {}
        assert second_plan.paths == {}


class TestTaskResults:
    """Task result behavior tests."""

    @pytest.mark.parametrize(
        ("update", "same_id"),
        [
            pytest.param({}, True, id="same-run-and-path"),
            pytest.param({"run_id": "other"}, False, id="new-run"),
            pytest.param({"bucket_path": "s3://b/other"}, False, id="new-path"),
        ],
    )
    def test_task_id_depends_on_run_and_path(
        self, update: dict[str, str], same_id: bool
    ) -> None:
        """Keep the task ID stable for a run and path and change it otherwise."""
        first = DumpTask(
            run_id="run",
            table="p.d.t",
            source="bigquery",
            target_schema="d",
            bucket_path="s3://b/t",
            selections=[AllSelection()],
        )
        second = first.model_copy(update=update)
        assert (first.task_id == second.task_id) is same_id


class TestPlanValidation:
    """Synchronization plan validation behavior tests."""

    def test_rejects_add_partition_without_current_partition(self) -> None:
        """Reject an add partition absent from the current manifest."""
        with pytest.raises(ValueError, match="Add/update partitions"):
            PartitionedTablePlan(
                table_signature="s",
                full_rebuild=False,
                current_partitions={},
                changes={
                    "1": PartitionChange(
                        kind="add",
                        partition_id="1",
                        path="path",
                        current=partition("1"),
                    )
                },
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
            changes={},
        )
        with pytest.raises(ValueError, match="ordinary and partitioned"):
            SyncPlan(
                schema_name="d",
                signatures={"p.d.t": "s"},
                paths={"p.d.t": ["path"]},
                partitioned_tables={"p.d.t": partitioned},
            )

    def test_accepts_previous_partition_on_update_change(self) -> None:
        """Accept a previous partition on an update change."""
        physical = partition("1")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={"1": physical},
            changes={
                "1": PartitionChange(
                    kind="update",
                    partition_id="1",
                    path="path",
                    previous=physical,
                    current=physical,
                )
            },
        )
        assert plan.changes["1"].previous == physical

    def test_rejects_removed_partition_in_current_manifest(self) -> None:
        """Reject a partition marked as both current and removed."""
        physical = partition("0")
        with pytest.raises(ValueError, match="Removed partitions"):
            PartitionedTablePlan(
                table_signature="s",
                full_rebuild=False,
                current_partitions={"0": physical},
                changes={
                    "0": PartitionChange(
                        kind="remove",
                        partition_id="0",
                        previous=physical,
                    )
                },
            )

    def test_reports_changed_publication_tables(self) -> None:
        """Report ordinary and partitioned tables with planned changes."""
        partitioned = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={},
            changes={},
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
