"""Typed test doubles for the data-proxy test suite."""

from dp.models import AllSelection, DumpTask, PartitionedTablePlan, SyncPlan


def sync_plan(
    *,
    run_id: str = "r1",
    schema_name: str = "app",
    signatures: dict[str, str] | None = None,
    paths: dict[str, list[str]] | None = None,
    partitioned_tables: dict[str, PartitionedTablePlan] | None = None,
    plans: list[SyncPlan] | None = None,
) -> SyncPlan:
    """Build one strict grouped synchronization plan for tests."""
    if plans is None and (signatures or paths or partitioned_tables):
        plans = [
            SyncPlan(
                schema_name=schema_name,
                signatures=signatures or {},
                paths=paths or {},
                partitioned_tables=partitioned_tables or {},
            )
        ]

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
