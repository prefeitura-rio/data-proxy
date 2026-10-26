from collections.abc import Callable
from datetime import date, timedelta
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from sync.models import (
    DuckLakePartition,
    DuckLakePartitionTransform,
    DuckLakeTableConfig,
    DumpResult,
    DumpStatus,
    DumpTask,
    FullTable,
    PartitionChange,
    PartitionedTable,
    PartitionedTablePlan,
    SchemaConfig,
    SourceConfig,
    SyncConfig,
    SyncPlan,
    SyncScope,
    UnitMapping,
)
from sync.sources.partitions import (
    AllSelection,
    PhysicalPartition,
    RangeSelection,
    TimeRangeSelection,
)
from sync.sources.registry import sources
from tests.fixtures.types import NoFallbackSource
from tests.helpers import partition
from tests.strategies import (
    identifiers,
    physical_partitions,
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

    def test_names_the_scratch_file_after_the_schema_and_the_table(self) -> None:
        """The scratch Parquet file of a table lives under the run bucket, the schema and the short name."""
        table = FullTable(
            name="p.d.events", resolved_schema="app", resolved_source="bigquery"
        )

        task = table.to_task("run-1", "bucket", "tmp", [AllSelection()])

        assert (table.table_name, task.bucket_path) == (
            "events",
            "s3://bucket/tmp/app/events/data.parquet",
        )

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


class TestSyncScope:
    """A sync scope picks the tables that one run plans."""

    CONFIG: ClassVar[SyncConfig] = SyncConfig(
        schemas={
            "one": SchemaConfig(
                tables=[FullTable(name="p.d.first"), FullTable(name="p.d.second")]
            ),
            "two": SchemaConfig(tables=[FullTable(name="p.d.third")]),
        }
    )

    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            pytest.param(
                SyncScope(),
                ["one.first", "one.second", "two.third"],
                id="empty-scope-picks-everything",
            ),
            pytest.param(
                SyncScope(schemas=["one"]), ["one.first", "one.second"], id="one-schema"
            ),
            pytest.param(
                SyncScope(tables=["one.second"]), ["one.second"], id="one-table"
            ),
            pytest.param(
                SyncScope(schemas=["two"], tables=["one.first"]),
                ["one.first", "two.third"],
                id="a-schema-and-a-table-of-another-schema",
            ),
            pytest.param(
                SyncScope(tables=["one.third"]), [], id="a-table-of-another-schema-only"
            ),
            pytest.param(SyncScope(schemas=["missing"]), [], id="unknown-schema"),
        ],
    )
    def test_keeps_only_the_tables_in_scope(
        self, scope: SyncScope, expected: list[str]
    ) -> None:
        """Keep the picked tables, and drop a schema that has none left."""
        scoped = self.CONFIG.scoped(scope)

        assert [
            f"{table.resolved_schema}.{table.table_name}" for table in scoped.tables
        ] == expected
        assert all(schema.tables for schema in scoped.schemas.values())


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


class TestDuckLakeConfigValidators:
    """The DuckLake settings of a table refuse a combination that DuckLake would refuse later."""

    def test_a_bucket_transform_needs_its_count(self) -> None:
        """Say why the partition is wrong."""
        with pytest.raises(ValidationError, match="require buckets"):
            DuckLakePartition(column="cpf", transform=DuckLakePartitionTransform.BUCKET)

    @pytest.mark.parametrize(
        "transform",
        [
            DuckLakePartitionTransform.IDENTITY,
            DuckLakePartitionTransform.YEAR,
            DuckLakePartitionTransform.MONTH,
            DuckLakePartitionTransform.DAY,
            DuckLakePartitionTransform.HOUR,
        ],
    )
    def test_no_other_transform_takes_a_count(
        self, transform: DuckLakePartitionTransform
    ) -> None:
        """Refuse a bucket count on a transform that has none."""
        with pytest.raises(
            ValidationError, match="Only bucket partition transforms accept buckets"
        ):
            DuckLakePartition(column="cpf", transform=transform, buckets=4)

    def test_a_bucket_transform_with_its_count_is_valid(self) -> None:
        """Accept the one valid shape of a bucket."""
        partition = DuckLakePartition(
            column="cpf", transform=DuckLakePartitionTransform.BUCKET, buckets=4
        )

        assert partition.buckets == 4

    def test_a_sort_that_is_empty_is_refused_and_a_missing_sort_is_not(self) -> None:
        """Tell an empty list, which is a mistake, from no sort."""
        assert DuckLakeTableConfig().sort is None
        with pytest.raises(ValidationError, match="at least one column"):
            DuckLakeTableConfig(sort=[])


class TestDumpResult:
    """A dump result says which files failed, and a failure must name at least one."""

    def test_a_failure_without_a_failed_path_is_refused(self) -> None:
        """Stop a failure that the publish step could not skip."""
        with pytest.raises(ValidationError, match="must have failed paths"):
            DumpResult(status=DumpStatus.FAILURE)

    def test_a_failure_with_its_paths_and_a_success_with_none_are_valid(self) -> None:
        """Accept the two shapes that the dump workflow returns."""
        assert DumpResult(
            status=DumpStatus.FAILURE, failed_paths=["s3://b/t"]
        ).failed_paths == ["s3://b/t"]
        assert DumpResult().failed_paths == []


class TestTableSignatureFields:
    """The fields of a signature are what a change of the configuration must resync."""

    def test_includes_the_settings_of_the_source_when_the_schema_has_some(self) -> None:
        """Resync a table when the settings of its source change."""
        plain = FullTable(name="p.d.t", resolved_source="bigquery")
        configured = FullTable(
            name="p.d.t",
            resolved_source="bigquery",
            resolved_source_settings={"project": "p"},
        )

        assert plain.config_signature_fields()["source"] == {"type": "bigquery"}
        assert configured.config_signature_fields()["source"] == {
            "type": "bigquery",
            "settings": {"project": "p"},
        }

    def test_leaves_the_rules_out_when_there_are_none_and_includes_them_otherwise(
        self,
    ) -> None:
        """Resync a table when its row rules change."""
        without = FullTable(name="p.d.t")
        with_rule = FullTable(
            name="p.d.t", rls=[UnitMapping(column="cpf", unit_type="cpf")]
        )

        assert without.config_signature_fields()["rls"] is None
        assert with_rule.config_signature_fields()["rls"] == [
            {"column": "cpf", "unit_type": "cpf"}
        ]
