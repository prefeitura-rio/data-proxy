"""Tests for synchronization planning operations."""

from hashlib import sha256
from unittest.mock import AsyncMock, patch

import pytest
from helpers import (
    FakeBigQueryClient,
    FakeDuckDBConnection,
    FakeRedis,
    bigquery_client,
    redis_client,
)

from dp.constants import SYNC_STATE_KEY
from dp.models import (
    AllTable,
    AllWithPartitionsTable,
    PartitionConfig,
    PartitionManifest,
    PhysicalPartition,
    SyncConfig,
    WindowTable,
)
from dp.planning import (
    build_partition_tasks,
    detect_changes,
    discover_json_columns,
    discover_partitions,
    expand_config,
    plan_partitioned_tables,
    plan_sync,
    table_signature,
)


def test_discovers_struct_columns() -> None:
    """STRUCT columns are selected for JSON conversion."""
    db = FakeDuckDBConnection(
        describe_rows=[
            ("cpf", "VARCHAR", None, None, None, None),
            ("units", "STRUCT(id VARCHAR)[]", None, None, None, None),
        ]
    )

    assert discover_json_columns(db, "p.d.t") == ["units"]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [("2025-01-13",), ("2025-01-15",), ("2025-01-14",)],
            ["2025-01-15", "2025-01-14"],
        ),
        ([], []),
    ],
)
def test_discover_partitions(rows: list[tuple[str]], expected: list[str]) -> None:
    """Only the configured latest window values are returned; none found yields none."""
    db = FakeDuckDBConnection(rows=rows)
    table = WindowTable(
        bq_table="p.d.t",
        partition=PartitionConfig(column="dt", n=2),
    )

    assert discover_partitions(db, table) == expected


def test_expands_dump_and_window_tables() -> None:
    """Dump and window tables retain their existing extraction behavior."""
    config = SyncConfig(
        tables=[
            AllTable(bq_table="p.d.dump"),
            AllWithPartitionsTable(bq_table="p.d.partitioned"),
            WindowTable(
                bq_table="p.d.window",
                partition=PartitionConfig(column="dt", n=2),
            ),
        ]
    )
    with patch(
        "dp.planning.discover_partitions",
        return_value=["2025-01-15", "2025-01-14"],
    ):
        tasks = expand_config(config, "bucket", "sync-1", FakeDuckDBConnection())

    assert [task.gcs_path for task in tasks] == [
        "s3://bucket/d/dump/data.parquet",
        "s3://bucket/d/window/2025-01-15/data.parquet",
        "s3://bucket/d/window/2025-01-14/data.parquet",
    ]


def test_signature_includes_sync_configuration() -> None:
    """Configuration changes invalidate a stored source timestamp."""
    table = AllTable(bq_table="p.d.t", pg_schema="other")
    expected_hash = sha256(table.model_dump_json().encode()).hexdigest()

    assert table_signature(table, "100") == f"100:{expected_hash}"


@pytest.mark.asyncio
async def test_detects_only_changed_tables_and_reuses_client() -> None:
    """One metadata client per project serves all configured tables."""
    config = SyncConfig(
        tables=[
            AllTable(bq_table="p.d.t1"),
            AllTable(bq_table="p.d.t2"),
            AllWithPartitionsTable(bq_table="p.d.partitioned"),
        ]
    )
    fake_redis = FakeRedis()
    fake_redis.store[SYNC_STATE_KEY.format(bq_table="p.d.t1")] = table_signature(
        config.tables[0], "100"
    )
    client = FakeBigQueryClient()

    with (
        patch(
            "dp.planning.Client",
            return_value=bigquery_client(client),
        ),
        patch("dp.planning.table_modified", side_effect=["100", "200"]),
    ):
        changed = await detect_changes(config, redis_client(fake_redis))

    assert changed == {"p.d.t2": table_signature(config.tables[1], "200")}
    assert client.close_calls == 1


def physical_partition(partition_id: str, signature: str) -> PhysicalPartition:
    """Return one normalized ten-value range partition."""
    lower = int(partition_id)
    return PhysicalPartition(
        partition_id=partition_id,
        column="cpf",
        lower=lower,
        upper=lower + 10,
        signature=signature,
    )


@pytest.mark.asyncio
async def test_plans_new_changed_and_removed_physical_partitions() -> None:
    """Only new and changed ranges create tasks; removed ranges stay in the plan."""
    table = AllWithPartitionsTable(bq_table="p.d.people")
    current = {
        "0": physical_partition("0", "same"),
        "10": physical_partition("10", "changed"),
        "30": physical_partition("30", "new"),
    }
    stored = PartitionManifest(
        table_signature="table",
        partitions={
            "0": physical_partition("0", "same"),
            "10": physical_partition("10", "old"),
            "20": physical_partition("20", "removed"),
        },
    )

    with (
        patch("dp.planning.Client", return_value=bigquery_client(FakeBigQueryClient())),
        patch("dp.planning.physical_partitions", return_value=("table", current)),
        patch(
            "dp.planning.read_partition_manifest",
            new_callable=AsyncMock,
            return_value=stored,
        ),
    ):
        plans, tasks = await plan_partitioned_tables(
            SyncConfig(tables=[table]),
            redis_client(FakeRedis()),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    plan = plans[table.bq_table]
    assert set(plan.changed_paths) == {"10", "30"}
    assert set(plan.removed_partitions) == {"20"}
    assert [task.selection.type for task in tasks] == ["range", "range"]
    assert tasks[0].gcs_path.endswith("/partitions/10/data.parquet")


def test_build_partition_tasks_orders_remainder_after_numeric_partitions() -> None:
    """A non-numeric remainder id sorts after every numeric partition id."""
    table = AllWithPartitionsTable(bq_table="p.d.people")
    remainder = PhysicalPartition(
        partition_id="__NULL__",
        column="cpf",
        lower=0,
        upper=100,
        signature="remainder-sig",
        is_remainder=True,
    )
    current = {
        "20": physical_partition("20", "sig-20"),
        "0": physical_partition("0", "sig-0"),
        "__NULL__": remainder,
    }

    paths, tasks = build_partition_tasks(
        table,
        current,
        {"0", "20", "__NULL__"},
        "sync-1",
        "bucket",
        [],
    )

    assert list(paths) == ["0", "20", "__NULL__"]
    assert [task.selection.type for task in tasks] == ["range", "range", "remainder"]


@pytest.mark.asyncio
async def test_unchanged_physical_partitions_create_no_work() -> None:
    """A matching committed manifest creates no partition plan or tasks."""
    table = AllWithPartitionsTable(bq_table="p.d.people")
    current = {"0": physical_partition("0", "same")}
    stored = PartitionManifest(table_signature="table", partitions=current)

    with (
        patch("dp.planning.Client", return_value=bigquery_client(FakeBigQueryClient())),
        patch("dp.planning.physical_partitions", return_value=("table", current)),
        patch(
            "dp.planning.read_partition_manifest",
            new_callable=AsyncMock,
            return_value=stored,
        ),
    ):
        plans, tasks = await plan_partitioned_tables(
            SyncConfig(tables=[table]),
            redis_client(FakeRedis()),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert plans == {}
    assert tasks == []


@pytest.mark.asyncio
async def test_partitioned_table_first_sync_forces_every_partition() -> None:
    """A missing manifest marks every current physical partition for extraction."""
    table = AllWithPartitionsTable(bq_table="p.d.people")
    current = {"0": physical_partition("0", "signature")}

    with (
        patch("dp.planning.Client", return_value=bigquery_client(FakeBigQueryClient())),
        patch("dp.planning.physical_partitions", return_value=("table", current)),
        patch(
            "dp.planning.read_partition_manifest",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        plans, tasks = await plan_partitioned_tables(
            SyncConfig(tables=[table]),
            redis_client(FakeRedis()),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert plans[table.bq_table].full_rebuild is True
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_builds_plan_with_exact_parquet_paths() -> None:
    """The plan contains only tables that produced extraction tasks."""
    config = SyncConfig(tables=[AllTable(bq_table="p.d.t")])
    fake_redis = FakeRedis()

    with patch(
        "dp.planning.detect_changes",
        new_callable=AsyncMock,
        return_value={"p.d.t": "signature"},
    ):
        plan, tasks = await plan_sync(
            config,
            redis_client(fake_redis),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert plan is not None
    assert plan.signatures == {"p.d.t": "signature"}
    assert plan.paths == {"p.d.t": ["s3://bucket/d/t/data.parquet"]}
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_returns_no_plan_when_changed_table_yields_no_tasks() -> None:
    """A changed window table with no discovered partitions produces no plan."""
    config = SyncConfig(
        tables=[
            WindowTable(
                bq_table="p.d.t",
                partition=PartitionConfig(column="dt", n=2),
            )
        ]
    )

    with (
        patch(
            "dp.planning.detect_changes",
            new_callable=AsyncMock,
            return_value={"p.d.t": "signature"},
        ),
        patch("dp.planning.discover_partitions", return_value=[]),
    ):
        plan, tasks = await plan_sync(
            config,
            redis_client(FakeRedis()),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert plan is None
    assert tasks == []


@pytest.mark.asyncio
async def test_returns_no_plan_when_nothing_changed() -> None:
    """An unchanged run creates no finalizer plan or tasks."""
    config = SyncConfig(tables=[AllTable(bq_table="p.d.t")])

    with patch(
        "dp.planning.detect_changes",
        new_callable=AsyncMock,
        return_value={},
    ):
        plan, tasks = await plan_sync(
            config,
            redis_client(FakeRedis()),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert plan is None
    assert tasks == []
