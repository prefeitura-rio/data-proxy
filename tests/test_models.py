"""Tests for the sync configuration data models."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from dp.models import (
    AllSelection,
    DumpFailure,
    DumpStatus,
    DumpSuccess,
    DumpTask,
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    SchemaConfig,
    SchemaWriters,
    SyncConfig,
    SyncPlan,
    SyncPublicationInput,
    TimeRangeSelection,
    UnitMapping,
    task_outcome_adapter,
)


def test_task_id_is_stable_for_one_run_and_path() -> None:
    """Equal run and path values produce the same task identity."""
    first = DumpTask(
        run_id="s1",
        table="p.d.t",
        bucket_path="s3://b/t.parquet",
        selection=AllSelection(),
    )
    second = DumpTask(
        run_id="s1",
        table="p.d.t",
        bucket_path="s3://b/t.parquet",
        selection=AllSelection(),
    )

    assert first.task_id == second.task_id


def test_task_id_changes_with_run_or_path() -> None:
    """A different run or path produces a different task identity."""
    task = DumpTask(
        run_id="s1",
        table="p.d.t",
        bucket_path="s3://b/t.parquet",
        selection=AllSelection(),
    )
    other_run = task.model_copy(update={"run_id": "s2"})
    other_path = task.model_copy(update={"bucket_path": "s3://b/other.parquet"})

    assert len({task.task_id, other_run.task_id, other_path.task_id}) == 3


def test_task_outcome_statuses_are_discriminated() -> None:
    """Each task outcome exposes its fixed status value."""
    assert DumpSuccess().status == DumpStatus.SUCCESS
    assert DumpFailure(failed_path="s3://b/failed").status == DumpStatus.FAILURE


@pytest.mark.parametrize(
    "values",
    [
        {"status": "success", "failed_path": "s3://b/failed"},
        {"status": "failure"},
    ],
)
def test_task_outcome_requires_matching_failed_path(values: dict[str, object]) -> None:
    """Task status and failed path must describe the same outcome."""
    with pytest.raises(ValidationError):
        task_outcome_adapter.validate_python(values)


def test_unit_mapping_pairs_column_and_type() -> None:
    """A unit mapping names one row column and its unit type."""
    mapping = UnitMapping(column="id_cras", unit_type="cras")

    assert mapping.column == "id_cras"
    assert mapping.unit_type == "cras"


@pytest.mark.parametrize(
    "value", [{"column": "", "unit_type": "unit"}, {"column": "id", "unit_type": ""}]
)
def test_unit_mapping_requires_column_and_type(value: dict[str, str]) -> None:
    """RLS mappings must identify both a source column and unit type."""
    with pytest.raises(ValidationError):
        UnitMapping.model_validate(value)


def test_table_accepts_a_list_of_unit_mappings() -> None:
    """A table's `rls` config is a plain list of column/unit_type pairs."""
    table = FullTable.model_validate(
        {
            "name": "p.d.t",
            "strategy": "full",
            "rls": [
                {"column": "id_cras", "unit_type": "cras"},
                {"column": "id_escola", "unit_type": "escola"},
            ],
        }
    )

    assert table.rls is not None
    assert len(table.rls) == 2
    assert table.rls[0].column == "id_cras"
    assert table.rls[1].unit_type == "escola"


def test_table_defaults_rls_to_none() -> None:
    """A table without an `rls` config is not protected by access_policy."""
    table = FullTable.model_validate({"name": "p.d.t", "strategy": "full"})

    assert table.rls is None


def test_sync_config_maps_schemas_to_their_identity_claim() -> None:
    """Each schema configures its own identity claim for access_policy checks."""
    config = SyncConfig.model_validate(
        {
            "schemas": {
                "app_x": {
                    "claim": "preferred_username",
                    "tables": [{"name": "p.d.t", "strategy": "full"}],
                }
            }
        }
    )

    assert config.schemas["app_x"].claim == "preferred_username"


def test_sync_config_rejects_reserved_freshness_table_name() -> None:
    """A source table cannot replace schema freshness metadata."""
    with pytest.raises(ValueError, match=r"freshness.*reserved"):
        SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.freshness")])}
        )


def test_sync_config_defaults_schemas_to_empty() -> None:
    """A config without a `schemas` section has no schemas or tables."""
    config = SyncConfig.model_validate({})

    assert config.schemas == {}
    assert config.tables == []


def test_sync_config_tables_flattens_every_schema() -> None:
    """The `tables` property lists every table across every schema."""
    config = SyncConfig.model_validate(
        {
            "schemas": {
                "app": {"tables": [{"name": "p.app.one", "strategy": "full"}]},
                "other": {"tables": [{"name": "p.other.two", "strategy": "full"}]},
            }
        }
    )

    assert [table.name for table in config.tables] == ["p.app.one", "p.other.two"]


def test_sync_config_stamps_resolved_schema_from_nesting_key() -> None:
    """A table's schema is whatever key it is nested under, not a field of its own."""
    config = SyncConfig.model_validate(
        {"schemas": {"app": {"tables": [{"name": "p.app.one", "strategy": "full"}]}}}
    )

    assert config.tables[0].resolved_schema == "app"


def test_sync_config_rejects_rls_table_in_schema_without_claim() -> None:
    """A schema with an rls table but no claim fails validation clearly."""
    with pytest.raises(ValidationError, match="no claim but rls tables"):
        SyncConfig.model_validate(
            {
                "schemas": {
                    "app": {
                        "tables": [
                            {
                                "name": "p.app.one",
                                "strategy": "full",
                                "rls": [{"column": "id_cras", "unit_type": "cras"}],
                            }
                        ]
                    }
                }
            }
        )


@pytest.mark.parametrize("name", ["p.d", "p.d.t.extra", "p.d.t; DROP TABLE x"])
def test_table_rejects_invalid_bigquery_reference(name: str) -> None:
    """Configured source names must be project.dataset.table references."""
    with pytest.raises(ValidationError, match="String should match pattern"):
        FullTable(name=name)


def test_sync_config_rejects_duplicate_source_table_names() -> None:
    """One source table cannot publish to multiple configured schemas."""
    with pytest.raises(ValidationError, match="Duplicate configured table names"):
        SyncConfig.model_validate(
            {
                "schemas": {
                    "one": {"tables": [{"name": "p.d.t", "strategy": "full"}]},
                    "two": {"tables": [{"name": "p.d.t", "strategy": "full"}]},
                }
            }
        )


@pytest.mark.parametrize("n", [0, -1])
def test_partitioned_rejects_non_positive_retention(n: int) -> None:
    """Partition retention must retain at least one time partition."""
    with pytest.raises(ValidationError):
        PartitionedTable(name="p.d.t", n=n)


@pytest.mark.parametrize(
    "value",
    [
        {"name": "", "columns": ["id"]},
        {"name": "idx", "columns": []},
        {"name": "idx", "columns": [""]},
    ],
)
def test_index_requires_name_and_columns(value: dict[str, object]) -> None:
    """An index definition must have a name and at least one column."""
    with pytest.raises(ValidationError):
        IndexConfig.model_validate(value)


@pytest.mark.parametrize(
    "selection",
    [
        pytest.param(
            lambda: RangeSelection(partition_id="0", column="id", lower=1, upper=1),
            id="range",
        ),
        pytest.param(
            lambda: RemainderSelection(column="id", start=1, end=1),
            id="remainder",
        ),
        pytest.param(
            lambda: TimeRangeSelection(
                column="dt", lower="2025-01-02", upper="2025-01-01"
            ),
            id="time",
        ),
    ],
)
def test_selection_rejects_invalid_bounds(selection: Callable[[], object]) -> None:
    """Every selection must have an ordered lower and upper bound."""
    with pytest.raises(ValidationError):
        selection()


def test_physical_partition_rejects_mismatched_range_id() -> None:
    """A range selection must describe its enclosing physical partition."""
    with pytest.raises(ValidationError, match="must match physical partition"):
        PhysicalPartition(
            partition_id="10",
            signature="signature",
            selection=RangeSelection(partition_id="0", column="id", lower=0, upper=10),
        )


def test_publication_input_rejects_plan_table_absent_from_config() -> None:
    """Publication cannot begin for a table outside the mounted configuration."""
    config = SyncConfig(schemas={"app": SchemaConfig(tables=[FullTable(name="p.d.t")])})
    plan = SyncPlan(
        schema_name="app",
        signatures={"p.d.other": "signature"},
        paths={"p.d.other": ["s3://bucket/other/data.parquet"]},
    )

    with pytest.raises(ValidationError, match="unknown tables"):
        SyncPublicationInput(config=config, plan=plan)


def test_schema_config_claim_defaults_to_none() -> None:
    """A schema without any rls tables does not require a claim."""
    schema = SchemaConfig()

    assert schema.claim is None
    assert schema.tables == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"signatures": {"p.d.t": "s"}, "paths": {}},
        {"signatures": {"p.d.t": "s"}, "paths": {"p.d.t": []}},
    ],
)
def test_sync_plan_rejects_invalid_paths(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"paths|ordinary"):
        SyncPlan.model_validate({"schema_name": "app", **kwargs})


def test_sync_plan_rejects_ordinary_partition_overlap() -> None:
    with pytest.raises(ValueError, match="ordinary"):
        SyncPlan(
            schema_name="app",
            signatures={"p.d.t": "s"},
            paths={"p.d.t": ["p"]},
            partitioned_tables={
                "p.d.t": PartitionedTablePlan(
                    table_signature="s",
                    full_rebuild=True,
                    current_partitions={},
                    changed_paths={},
                    removed_partitions={},
                )
            },
        )


def test_schema_writers_reject_missing_schema() -> None:
    with pytest.raises(RuntimeError, match="not configured"):
        SchemaWriters(writers={}).dsn("missing")


def test_partition_plan_rejects_invalid_sets() -> None:
    partition = PhysicalPartition(
        partition_id="1",
        signature="s",
        selection=RangeSelection(partition_id="1", column="id", lower=1, upper=2),
    )
    cases: list[dict[str, object]] = [
        {"changed_paths": {"2": "p"}, "current_partitions": {"1": partition}},
        {"previous_partitions": {"1": partition}, "changed_paths": {}},
        {
            "removed_partitions": {"1": partition},
            "current_partitions": {"1": partition},
        },
    ]
    for update in cases:
        base: dict[str, object] = {
            "table_signature": "s",
            "full_rebuild": False,
            "current_partitions": {},
            "changed_paths": {},
            "previous_partitions": {},
            "removed_partitions": {},
        }
        base.update(update)
        with pytest.raises(ValueError, match="partition"):
            PartitionedTablePlan.model_validate(base)
