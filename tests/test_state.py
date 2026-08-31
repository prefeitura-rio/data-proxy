from dp.state import cleanup_run

# ruff: noqa: E402
"""Tests for current run state."""
import pytest

from dp.models import (
    AllSelection,
    DumpFailure,
    DumpSuccess,
    DumpTask,
    Strategy,
    SyncPlan,
    TableState,
)
from dp.state import (
    cleanup_consumer,
    complete_dump,
    complete_schema,
    create_consumer_group,
    create_run,
    ensure_groups,
    read_active_run,
    read_partition_manifest,
    read_remaining,
    read_sync_plan,
    read_sync_plans,
    read_table_signature,
    read_table_state,
)
from tests.helpers import FakeRedis


@pytest.mark.asyncio
async def test_run_stores_schema_plans_in_hash(redis: FakeRedis) -> None:
    fake = redis
    plan = SyncPlan(
        schema_name="app", signatures={"p.d.t": "s"}, paths={"p.d.t": ["s3://b/t"]}
    )
    assert await create_run((fake), "r1", [plan], 1)
    assert await read_sync_plan((fake), "r1", "app") == plan


@pytest.mark.asyncio
async def test_dump_result_is_idempotent(redis: FakeRedis) -> None:
    fake = redis
    fake.store["dp:remaining:r1"] = "1"
    task = DumpTask(
        run_id="r1", table="p.d.t", bucket_path="s3://b/t", selection=AllSelection()
    )
    assert await complete_dump((fake), task, DumpFailure(failed_path="s3://b/t")) == 0
    assert await complete_dump((fake), task, DumpSuccess()) is None


@pytest.mark.asyncio
async def test_schema_completion_removes_one_plan_and_counts_remaining(
    redis: FakeRedis,
) -> None:
    fake = redis
    plans_key = "dp:plans:r1"
    fake.hashes[plans_key] = {"app": "{}", "other": "{}"}
    states = {"p.d.t": TableState(strategy=Strategy.FULL, signature="s")}
    assert await complete_schema((fake), "r1", "app", states) == 1
    assert "app" not in fake.hashes[plans_key]
    assert await complete_schema((fake), "r1", "app", states) is None


"""State boundary and error coverage."""
from redis.exceptions import ResponseError

from tests.helpers import FakeRedisGroup


@pytest.mark.asyncio
async def test_state_reads_missing_and_present_values(redis: FakeRedis) -> None:
    fake = redis
    assert await read_table_state((fake), "p.d.t") is None
    assert await read_table_signature((fake), "p.d.t") is None
    assert await read_partition_manifest((fake), "p.d.t") is None
    assert await read_active_run(fake) is None
    assert await read_remaining((fake), "r") is None
    fake.store["dp:active"] = "r"
    fake.store["dp:remaining:r"] = "2"
    fake.store["dp:state:p.d.t"] = TableState(
        strategy=Strategy.FULL, signature="s"
    ).model_dump_json()
    assert await read_active_run(fake) == "r"
    assert await read_remaining((fake), "r") == 2
    assert await read_table_signature((fake), "p.d.t") == "s"


@pytest.mark.asyncio
async def test_group_busy_is_ignored() -> None:
    await create_consumer_group(
        (FakeRedisGroup(side_effect=ResponseError("BUSYGROUP"))), "s", "g"
    )


@pytest.mark.asyncio
async def test_cleanup_consumer_keeps_pending_and_deletes_idle() -> None:
    pending = FakeRedis(pending_consumers={"c"})
    await cleanup_consumer((pending), "s", "g", "c")
    assert pending.deleted_consumers == []
    idle = FakeRedis()
    await cleanup_consumer((idle), "s", "g", "c")
    assert idle.deleted_consumers == [("s", "g", "c")]


@pytest.mark.asyncio
async def test_dump_missing_and_invalid_counter(redis: FakeRedis) -> None:
    task = DumpTask(
        run_id="r", table="p.d.t", bucket_path="s3://b", selection=AllSelection()
    )
    with pytest.raises(RuntimeError, match="Remaining task count"):
        await complete_dump((FakeRedis()), task, DumpSuccess())
    fake = redis
    fake.store["dp:remaining:r"] = "0"
    with pytest.raises(RuntimeError, match="Invalid remaining"):
        await complete_dump((fake), task, DumpSuccess())


@pytest.mark.asyncio
async def test_state_readers_and_group_setup(redis: FakeRedis) -> None:
    fake = redis
    fake.store["dp:active"] = "r1"
    fake.store["dp:remaining:r1"] = "2"
    fake.store["dp:state:p.d.t"] = TableState(
        strategy=Strategy.FULL, signature="s"
    ).model_dump_json()
    fake.hashes["dp:plans:r1"] = {"app": SyncPlan(schema_name="app").model_dump_json()}
    assert await read_active_run(fake) == "r1"
    assert await read_remaining((fake), "r1") == 2
    assert await read_table_signature((fake), "p.d.t") == "s"
    assert await read_table_state((fake), "p.d.t") is not None
    assert await read_partition_manifest((fake), "p.d.t") is None
    assert len(await read_sync_plans((fake), "r1")) == 1
    await ensure_groups(fake)


@pytest.mark.asyncio
async def test_state_cleanup_and_idle_consumer(redis: FakeRedis) -> None:
    fake = redis
    fake.store.update({"dp:active": "r1", "dp:remaining:r1": "0"})
    await cleanup_consumer((fake), "stream", "group", "consumer")
    await cleanup_run((fake), "r1")
    assert fake.store == {}


@pytest.mark.asyncio
async def test_partition_manifest_reader_returns_manifest(redis: FakeRedis) -> None:
    fake = redis
    fake.store["dp:state:p.d.t"] = TableState(
        strategy=Strategy.PARTITIONED, signature="s", partitions={}
    ).model_dump_json()
    assert await read_partition_manifest((fake), "p.d.t") is not None


@pytest.mark.asyncio
async def test_create_run_rejects_active_run(redis: FakeRedis) -> None:
    fake = redis
    fake.store["dp:active"] = "old"
    assert await create_run((fake), "new", [], 0) is False
