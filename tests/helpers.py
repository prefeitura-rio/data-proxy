"""Shared test builders and assertions for the data-proxy test suite."""

from typing import cast

from psycopg.sql import Composable

from dp.models import (
    AllSelection,
    DumpTask,
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    SyncPlan,
)


def sync_plan(
    *,
    schema_name: str = "app",
    signatures: dict[str, str] | None = None,
    paths: dict[str, list[str]] | None = None,
    partitioned_tables: dict[str, PartitionedTablePlan] | None = None,
) -> SyncPlan:
    """Build one synchronization plan for tests."""
    return SyncPlan(
        schema_name=schema_name,
        signatures=signatures or {},
        paths=paths or {},
        partitioned_tables=partitioned_tables or {},
    )


def dump(
    *,
    run_id: str = "r1",
    table: str = "p.d.t",
    bucket_path: str = "s3://b/t",
) -> DumpTask:
    """Build one common dump task for tests."""
    return DumpTask(
        run_id=run_id,
        table=table,
        bucket_path=bucket_path,
        selection=AllSelection(),
    )


def partition(
    partition_id: str,
    signature: str = "signature",
    *,
    column: str = "cpf",
    width: int = 10,
) -> PhysicalPartition:
    """Build one normalized integer range partition for tests."""
    lower = int(partition_id)
    return PhysicalPartition(
        partition_id=partition_id,
        signature=signature,
        selection=RangeSelection(
            partition_id=partition_id,
            column=column,
            lower=lower,
            upper=lower + width,
        ),
    )


def planning_partition(partition_id: str, signature: str = "s") -> PhysicalPartition:
    """Build one planning partition, including the null remainder bucket."""
    selection = (
        RemainderSelection(column="id", start=0, end=1)
        if partition_id == "__NULL__"
        else RangeSelection(
            partition_id=partition_id,
            column="id",
            lower=int(partition_id),
            upper=int(partition_id) + 1,
        )
    )

    return PhysicalPartition(
        partition_id=partition_id,
        signature=signature,
        selection=selection,
    )


def render(value: object) -> str:
    """Render a mapping value expected to be a Psycopg SQL object."""
    return cast(Composable, value).as_string(None)
