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
    FullTable,
    PartitionedTable,
    PartitionManifest,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    SyncConfig,
)
from dp.planning import (
    build_partition_tasks,
    build_sync_plan,
    detect_changes,
    discover_json_columns,
    expand_config,
    plan_partitioned_tables,
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


def test_expands_only_full_tables() -> None:
    """Only full tables are expanded into whole-table extraction tasks."""
    config = SyncConfig(
        tables=[
            FullTable(name="p.d.dump"),
            PartitionedTable(name="p.d.partitioned"),
        ]
    )

    tasks = expand_config(config, "bucket", "sync-1", FakeDuckDBConnection())

    assert [task.bucket_path for task in tasks] == [
        "s3://bucket/d/dump/data.parquet",
    ]


def test_signature_includes_sync_configuration() -> None:
    """Configuration changes invalidate a stored source timestamp."""
    table = FullTable(name="p.d.t", pg_schema="other")
    expected_hash = sha256(table.model_dump_json().encode()).hexdigest()

    assert table_signature(table, "100") == f"100:{expected_hash}"


@pytest.mark.asyncio
async def test_detects_only_changed_tables_and_reuses_client() -> None:
    """One metadata client per project serves all configured full tables."""
    config = SyncConfig(
        tables=[
            FullTable(name="p.d.t1"),
            FullTable(name="p.d.t2"),
            PartitionedTable(name="p.d.partitioned"),
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
        signature=signature,
        selection=RangeSelection(
            partition_id=partition_id, column="cpf", lower=lower, upper=lower + 10
        ),
    )


@pytest.mark.asyncio
async def test_plans_new_changed_and_removed_physical_partitions() -> None:
    """Only new and changed ranges create tasks; removed ranges stay in the plan."""
    table = PartitionedTable(name="p.d.people")
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

    plan = plans[table.name]
    assert set(plan.changed_paths) == {"10", "30"}
    assert set(plan.removed_partitions) == {"20"}
    assert [task.selection.type for task in tasks] == ["range", "range"]
    assert tasks[0].bucket_path.endswith("/partitions/10/data.parquet")


def test_build_partition_tasks_orders_remainder_after_numeric_partitions() -> None:
    """A non-numeric remainder id sorts after every numeric partition id."""
    table = PartitionedTable(name="p.d.people")
    remainder = PhysicalPartition(
        partition_id="__NULL__",
        signature="remainder-sig",
        selection=RemainderSelection(column="cpf", start=0, end=100),
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
    table = PartitionedTable(name="p.d.people")
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
    table = PartitionedTable(name="p.d.people")
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

    assert plans[table.name].full_rebuild is True
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_plan_partitioned_table_passes_configured_n() -> None:
    """The configured n is forwarded to physical partition discovery."""
    table = PartitionedTable(name="p.d.people", n=5)
    current = {"20250101": physical_partition("0", "signature")}

    with (
        patch("dp.planning.Client", return_value=bigquery_client(FakeBigQueryClient())),
        patch(
            "dp.planning.physical_partitions", return_value=("table", current)
        ) as physical_partitions,
        patch(
            "dp.planning.read_partition_manifest",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await plan_partitioned_tables(
            SyncConfig(tables=[table]),
            redis_client(FakeRedis()),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert physical_partitions.call_args.args[-1] == 5


@pytest.mark.asyncio
async def test_builds_plan_with_exact_parquet_paths() -> None:
    """The plan contains only tables that produced extraction tasks."""
    config = SyncConfig(tables=[FullTable(name="p.d.t")])
    fake_redis = FakeRedis()

    with patch(
        "dp.planning.detect_changes",
        new_callable=AsyncMock,
        return_value={"p.d.t": "signature"},
    ):
        plan, tasks = await build_sync_plan(
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
async def test_returns_no_plan_when_nothing_changed() -> None:
    """An unchanged run creates no finalizer plan or tasks."""
    config = SyncConfig(tables=[FullTable(name="p.d.t")])

    with patch(
        "dp.planning.detect_changes",
        new_callable=AsyncMock,
        return_value={},
    ):
        plan, tasks = await build_sync_plan(
            config,
            redis_client(FakeRedis()),
            "sync-1",
            "bucket",
            FakeDuckDBConnection(),
        )

    assert plan is None
    assert tasks == []
