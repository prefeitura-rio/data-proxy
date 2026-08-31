from dp.planning import expand_config

# ruff: noqa: E402
# ruff: noqa: E402
"""Tests for planning result types."""
import pytest

from dp.models import (
    PartitionedTable,
    PartitionedTablePlan,
    SchemaConfig,
    SyncConfig,
    SyncWork,
)
from dp.planning import build_sync_work
from tests.helpers import FakeBigQueryClient, FakeDuckDBConnection, FakeRedis


@pytest.mark.asyncio
async def test_build_sync_work_groups_partitioned_plan() -> None:
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[PartitionedTable(name="p.app.t")])}
    )
    table_plan = PartitionedTablePlan(
        table_signature="s",
        full_rebuild=True,
        current_partitions={},
        changed_paths={},
        removed_partitions={},
    )
    with (
        patch("dp.planning.detect_changes", new_callable=AsyncMock, return_value={}),
        patch(
            "dp.planning.plan_partitioned_tables",
            new_callable=AsyncMock,
            return_value=({"p.app.t": table_plan}, []),
        ),
    ):
        work = await build_sync_work(
            config, (FakeRedis()), "r", "b", FakeDuckDBConnection()
        )
    assert work.plans[0].partitioned_tables["p.app.t"] == table_plan


def test_sync_work_has_named_parts() -> None:
    work = SyncWork(plans=[], tasks=[])
    assert work.plans == []
    assert work.tasks == []


"""Coverage tests for planning branches."""
from collections.abc import Callable
from contextlib import nullcontext
from unittest.mock import AsyncMock, patch

from dp.models import (
    AllSelection,
    FullTable,
    PartitionManifest,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TableConfig,
)
from dp.planning import (
    build_partition_tasks,
    detect_changes,
    discover_json_columns,
    partition_changes,
    plan_partitioned_table,
    plan_partitioned_tables,
    table_signature,
)


def part(pid: str, sig: str = "s") -> PhysicalPartition:
    selection = (
        RemainderSelection(column="id", start=0, end=1)
        if pid == "__NULL__"
        else RangeSelection(
            partition_id=pid, column="id", lower=int(pid), upper=int(pid) + 1
        )
    )
    return PhysicalPartition(partition_id=pid, signature=sig, selection=selection)


@pytest.mark.asyncio
async def test_plan_partitioned_table_builds_changed_plan() -> None:
    table = PartitionedTable(name="p.d.t")
    current = {
        "1": PhysicalPartition(
            partition_id="1",
            signature="new",
            selection=RangeSelection(partition_id="1", column="id", lower=1, upper=2),
        )
    }
    with (
        patch("dp.planning.physical_partitions", return_value=("sig", current)),
        patch(
            "dp.planning.read_partition_manifest",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("dp.planning.discover_json_columns", return_value=[]),
    ):
        plan, tasks = await plan_partitioned_table(
            table,
            FakeBigQueryClient(),
            (FakeRedis()),
            "r",
            "b",
            FakeDuckDBConnection(),
        )
    assert plan is not None
    assert tasks


@pytest.mark.asyncio
async def test_plan_partitioned_tables_groups_partitioned_tables() -> None:
    config = SyncConfig(
        schemas={"d": SchemaConfig(tables=[PartitionedTable(name="p.d.t")])}
    )
    table_plan = PartitionedTablePlan(
        table_signature="s",
        full_rebuild=True,
        current_partitions={},
        changed_paths={},
        removed_partitions={},
    )
    with (
        patch(
            "dp.planning.bigquery_clients",
            return_value=nullcontext(client_factory(FakeBigQueryClient())),
        ),
        patch(
            "dp.planning.plan_partitioned_table",
            new_callable=AsyncMock,
            return_value=(table_plan, []),
        ),
    ):
        plans, tasks = await plan_partitioned_tables(
            config, (FakeRedis()), "r", "b", FakeDuckDBConnection()
        )
    assert plans == {"p.d.t": table_plan}
    assert tasks == []


@pytest.mark.parametrize(
    ("stored", "signature", "rebuild", "changed", "removed"),
    [
        (None, "s", True, {"1"}, set[str]()),
        (
            PartitionManifest(table_signature="s", partitions={"1": part("1", "new")}),
            "s",
            False,
            set[str](),
            set[str](),
        ),
        (
            PartitionManifest(table_signature="old", partitions={"1": part("1")}),
            "s",
            True,
            {"1"},
            set[str](),
        ),
        (
            PartitionManifest(table_signature="s", partitions={"2": part("2")}),
            "s",
            False,
            {"1"},
            {"2"},
        ),
    ],
)
def test_partition_changes_table_cases(
    stored: PartitionManifest | None,
    signature: str,
    rebuild: bool,
    changed: set[str],
    removed: set[str],
) -> None:
    result = partition_changes({"1": part("1", "new")}, stored, signature)
    assert (result.full_rebuild, result.changed, result.removed) == (
        rebuild,
        changed,
        removed,
    )


def test_expand_config_and_json_columns() -> None:
    db = FakeDuckDBConnection(rows=[("payload", "STRUCT(x INT)"), ("name", "VARCHAR")])
    config: list[TableConfig] = [FullTable(name="p.d.t")]
    tasks = expand_config(config, "bucket", "run", db)
    assert tasks[0].json_columns == []
    assert table_signature(config[0], "modified").startswith("modified:")


def test_partition_tasks_order_numeric_before_remainder() -> None:
    table = PartitionedTable(name="p.d.t")
    current = {"10": part("10"), "2": part("2"), "__NULL__": part("__NULL__")}
    batch = build_partition_tasks(table, current, set(current), "run", "bucket", [])
    assert list(batch.paths) == ["2", "10", "__NULL__"]


def test_discover_json_columns() -> None:
    db = FakeDuckDBConnection(rows=[("a", "struct(x)"), ("b", "VARCHAR")])
    assert discover_json_columns(db, "p.d.t") == []


@pytest.mark.asyncio
async def test_detect_changes_filters_unchanged_and_partitioned() -> None:
    config = SyncConfig(
        schemas={
            "d": SchemaConfig(
                tables=[FullTable(name="p.d.t"), PartitionedTable(name="p.d.p")]
            )
        }
    )
    client = FakeBigQueryClient()
    with (
        patch(
            "dp.planning.bigquery_clients",
            return_value=nullcontext(client_factory(client)),
        ),
        patch("dp.planning.table_modified", return_value="m"),
        patch(
            "dp.planning.read_table_signature",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await detect_changes(config, (FakeRedis()))
    assert set(result) == {"p.d.t"}


@pytest.mark.asyncio
async def test_partitioned_without_changes_returns_none() -> None:
    table = PartitionedTable(name="p.d.t")
    with (
        patch(
            "dp.planning.physical_partitions",
            return_value=("s", {"1": part("1", "new")}),
        ),
        patch(
            "dp.planning.read_partition_manifest",
            new_callable=AsyncMock,
            return_value=PartitionManifest(
                table_signature="s", partitions={"1": part("1", "new")}
            ),
        ),
    ):
        plan, tasks = await plan_partitioned_table(
            table,
            FakeBigQueryClient(),
            (FakeRedis()),
            "run",
            "bucket",
            FakeDuckDBConnection(),
        )
    assert plan is None
    assert tasks == []


@pytest.mark.asyncio
async def test_partitioned_tables_skips_full_tables() -> None:
    config = SyncConfig(schemas={"d": SchemaConfig(tables=[FullTable(name="p.d.t")])})
    with patch(
        "dp.planning.bigquery_clients",
        return_value=nullcontext(client_factory(FakeBigQueryClient())),
    ):
        plans, tasks = await plan_partitioned_tables(
            config, (FakeRedis()), "run", "bucket", FakeDuckDBConnection()
        )
    assert plans == {}
    assert tasks == []


"""Coverage for planning orchestration branches."""


@pytest.mark.asyncio
async def test_build_sync_work_no_changes() -> None:
    config = SyncConfig(schemas={})
    with (
        patch("dp.planning.detect_changes", new_callable=AsyncMock, return_value={}),
        patch(
            "dp.planning.plan_partitioned_tables",
            new_callable=AsyncMock,
            return_value=({}, []),
        ),
    ):
        result = await build_sync_work(
            config, (FakeRedis()), "r1", "b", FakeDuckDBConnection()
        )
    assert result == SyncWork(plans=[], tasks=[])


@pytest.mark.asyncio
async def test_build_sync_work_groups_full_table_by_schema() -> None:
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.t")])}
    )
    task = config.tables[0].to_task("r1", "b", AllSelection())
    with (
        patch(
            "dp.planning.detect_changes",
            new_callable=AsyncMock,
            return_value={"p.app.t": "sig"},
        ),
        patch("dp.planning.expand_config", return_value=[task]),
        patch(
            "dp.planning.plan_partitioned_tables",
            new_callable=AsyncMock,
            return_value=({}, []),
        ),
    ):
        result = await build_sync_work(
            config, (FakeRedis()), "r1", "b", FakeDuckDBConnection()
        )
    assert len(result.plans) == 1
    assert result.plans[0].schema_name == "app"
    assert result.tasks == [task]


"""Pipeline branch coverage."""


def client_factory(
    client: FakeBigQueryClient,
) -> Callable[[str], FakeBigQueryClient]:
    def get_client(_: str) -> FakeBigQueryClient:
        return client

    return get_client
