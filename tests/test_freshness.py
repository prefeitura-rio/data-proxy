"""Freshness edge coverage."""

from psycopg import Connection
from whenever import Instant

from dp.freshness import (
    delete_freshness,
    record_table_failures,
    update_published_freshness,
    upsert_freshness,
)
from dp.models import (
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
    SyncPlan,
)


def test_empty_freshness_batches_do_nothing(
    postgres: Connection[tuple[object, ...]],
) -> None:
    table = FullTable(name="p.app.t", resolved_schema="app")
    attempted_at = Instant.now()

    upsert_freshness(postgres, table, set(), attempted_at, success=True)
    delete_freshness(postgres, table, set())

    assert postgres.execute("SELECT 1").fetchone() == (1,)


def partition(partition_id: str) -> PhysicalPartition:
    """Build one range partition for freshness tests."""
    lower = int(partition_id)
    return PhysicalPartition(
        partition_id=partition_id,
        signature=f"signature-{partition_id}",
        selection=RangeSelection(
            partition_id=partition_id,
            column="id",
            lower=lower,
            upper=lower + 1,
        ),
    )


def test_update_published_freshness_replaces_full_table_rows(
    postgres: Connection[tuple[object, ...]],
) -> None:
    """Full publication replaces all existing freshness rows with the table row."""
    table = FullTable(name="p.app.t", resolved_schema="app")
    attempted_at = Instant.now()
    upsert_freshness(postgres, table, {"old"}, attempted_at, success=True)

    update_published_freshness(
        postgres,
        table,
        SyncPlan(
            schema_name="app",
            signatures={"p.app.t": "signature"},
            paths={"p.app.t": ["s3://b/t"]},
        ),
        set(),
        attempted_at,
    )

    assert postgres.execute(
        'SELECT partition, status::text FROM app.freshness WHERE "table" = %s',
        ("t",),
    ).fetchall() == [(None, "success")]


def test_update_published_freshness_records_partition_results(
    postgres: Connection[tuple[object, ...]],
) -> None:
    """Partition publication records successful, failed, and removed partitions."""
    table = PartitionedTable(name="p.app.t", resolved_schema="app")
    first = partition("1")
    second = partition("2")
    removed = partition("3")
    plan = SyncPlan(
        schema_name="app",
        partitioned_tables={
            "p.app.t": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"1": first, "2": second},
                changed_paths={"1": "s3://b/1", "2": "s3://b/2"},
                removed_partitions={"3": removed},
            )
        },
    )
    attempted_at = Instant.now()
    upsert_freshness(postgres, table, {"3"}, attempted_at, success=True)

    update_published_freshness(postgres, table, plan, {"2"}, attempted_at)

    assert postgres.execute(
        'SELECT partition, status::text FROM app.freshness WHERE "table" = %s ORDER BY partition',
        ("t",),
    ).fetchall() == [("1", "success"), ("2", "failure")]


def test_record_table_failures_uses_selected_partitions(
    postgres: Connection[tuple[object, ...]],
) -> None:
    """Failure records use explicit partitions or the plan's changed partitions."""
    full = FullTable(name="p.app.full", resolved_schema="app")
    partitioned = PartitionedTable(name="p.app.partitioned", resolved_schema="app")
    plan = SyncPlan(
        schema_name="app",
        partitioned_tables={
            "p.app.partitioned": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"1": partition("1")},
                changed_paths={"1": "s3://b/1"},
                removed_partitions={},
            )
        },
    )

    record_table_failures(
        postgres,
        [full, partitioned],
        plan,
        Instant.now(),
        {"p.app.full": {"override"}},
    )

    assert postgres.execute(
        'SELECT "table", partition, status::text FROM app.freshness ORDER BY "table"'
    ).fetchall() == [
        ("full", "override", "failure"),
        ("partitioned", "1", "failure"),
    ]
