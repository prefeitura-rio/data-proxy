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
from dp.settings import settings
from dp.state import (
    cleanup_consumer,
    cleanup_run,
    complete_dump,
    complete_schema,
    create_consumer_group,
    create_run,
    ensure_groups,
    read_active_run,
    read_failed_paths,
    read_partition_manifest,
    read_remaining,
    read_sync_plan,
    read_sync_plans,
    read_table_signature,
    read_table_state,
)

pytestmark = pytest.mark.usefixtures("test_settings")


class TestState:
    """Tests for synchronization state behavior."""

    @pytest.mark.asyncio
    async def test_run_stores_schema_plans_in_hash(
        self,
    ) -> None:
        """Verify run stores schema plans in hash."""
        plan = SyncPlan(
            schema_name="app", signatures={"p.d.t": "s"}, paths={"p.d.t": ["s3://b/t"]}
        )
        assert await create_run(settings.redis, "r1", [plan], 1)
        assert await read_sync_plan(settings.redis, "r1", "app") == plan

    @pytest.mark.asyncio
    async def test_dump_result_is_idempotent(
        self,
    ) -> None:
        """Verify dump result is idempotent."""
        fake = settings.redis
        await fake.set("dp:remaining:r1", "1")
        task = DumpTask(
            run_id="r1", table="p.d.t", bucket_path="s3://b/t", selection=AllSelection()
        )
        assert (
            await complete_dump(
                settings.redis, task, DumpFailure(failed_path="s3://b/t")
            )
            == 0
        )
        assert await complete_dump(settings.redis, task, DumpSuccess()) is None

    @pytest.mark.asyncio
    async def test_schema_completion_removes_one_plan_and_counts_remaining(
        self,
    ) -> None:
        """Verify schema completion removes one plan and counts remaining."""
        fake = settings.redis
        plans_key = "dp:plans:r1"
        await fake.hset(plans_key, mapping={"app": "{}", "other": "{}"})
        states = {"p.d.t": TableState(strategy=Strategy.FULL, signature="s")}
        assert await complete_schema(settings.redis, "r1", "app", states) == 1
        assert await fake.hexists(plans_key, "app") is False
        assert await complete_schema(settings.redis, "r1", "app", states) is None

    """State boundary and error coverage."""

    @pytest.mark.asyncio
    async def test_state_reads_missing_and_present_values(
        self,
    ) -> None:
        """Verify state reads missing and present values."""
        fake = settings.redis
        assert await read_table_state(settings.redis, "p.d.t") is None
        assert await read_table_signature(settings.redis, "p.d.t") is None
        assert await read_partition_manifest(settings.redis, "p.d.t") is None
        assert await read_active_run(settings.redis) is None
        assert await read_remaining(settings.redis, "r") is None
        await fake.set("dp:active", "r")
        await fake.set("dp:remaining:r", "2")
        await fake.set(
            "dp:state:p.d.t",
            TableState(strategy=Strategy.FULL, signature="s").model_dump_json(),
        )
        assert await read_active_run(settings.redis) == "r"
        assert await read_remaining(settings.redis, "r") == 2
        assert await read_table_signature(settings.redis, "p.d.t") == "s"

    @pytest.mark.asyncio
    async def test_group_busy_is_ignored(
        self,
    ) -> None:
        """Verify group busy is ignored."""
        client = settings.redis
        await create_consumer_group(client, "s", "g")
        await create_consumer_group(client, "s", "g")

    @pytest.mark.asyncio
    async def test_dump_missing_and_invalid_counter(
        self,
    ) -> None:
        """Verify dump missing and invalid counter."""
        task = DumpTask(
            run_id="r", table="p.d.t", bucket_path="s3://b", selection=AllSelection()
        )
        with pytest.raises(RuntimeError, match="Remaining task count"):
            await complete_dump((settings.redis), task, DumpSuccess())
        fake = settings.redis
        await fake.set("dp:remaining:r", "0")
        with pytest.raises(RuntimeError, match="Invalid remaining"):
            await complete_dump(settings.redis, task, DumpSuccess())

    @pytest.mark.asyncio
    async def test_state_readers_and_group_setup(
        self,
    ) -> None:
        """Verify state readers and group setup."""
        fake = settings.redis
        await fake.set("dp:active", "r1")
        await fake.set("dp:remaining:r1", "2")
        await fake.set(
            "dp:state:p.d.t",
            TableState(strategy=Strategy.FULL, signature="s").model_dump_json(),
        )
        await fake.hset(
            "dp:plans:r1", "app", SyncPlan(schema_name="app").model_dump_json()
        )
        assert await read_active_run(settings.redis) == "r1"
        assert await read_remaining(settings.redis, "r1") == 2
        assert await read_table_signature(settings.redis, "p.d.t") == "s"
        assert await read_table_state(settings.redis, "p.d.t") is not None
        assert await read_partition_manifest(settings.redis, "p.d.t") is None
        assert len(await read_sync_plans(settings.redis, "r1")) == 1
        await ensure_groups(settings.redis)

    @pytest.mark.asyncio
    async def test_state_cleanup_and_idle_consumer(
        self,
    ) -> None:
        """Verify state cleanup and idle consumer."""
        fake = settings.redis
        await fake.mset({"dp:active": "r1", "dp:remaining:r1": "0"})
        await create_consumer_group(settings.redis, "stream", "group")
        await cleanup_consumer(settings.redis, "stream", "group", "consumer")
        await cleanup_run(settings.redis, "r1")
        assert (
            await fake.exists(
                "dp:active", "dp:plans:r1", "dp:remaining:r1", "dp:results:r1"
            )
            == 0
        )

    @pytest.mark.asyncio
    async def test_cleanup_consumer_keeps_pending_messages(
        self,
    ) -> None:
        """Verify cleanup consumer keeps pending messages."""
        stream = "pending-stream"
        group = "pending-group"
        consumer = "pending-consumer"
        fake = settings.redis
        await fake.xadd(stream, {"payload": "value"})
        await fake.xgroup_create(stream, group, id="0")
        await fake.xreadgroup(group, consumer, {stream: ">"})

        await cleanup_consumer(settings.redis, stream, group, consumer)

        assert await fake.xpending_range(stream, group, "-", "+", 10, consumer) != []

    @pytest.mark.asyncio
    async def test_partition_manifest_reader_returns_manifest(
        self,
    ) -> None:
        """Verify partition manifest reader returns manifest."""
        fake = settings.redis
        await fake.set(
            "dp:state:p.d.t",
            TableState(
                strategy=Strategy.PARTITIONED, signature="s", partitions={}
            ).model_dump_json(),
        )
        assert await read_partition_manifest(settings.redis, "p.d.t") is not None

    @pytest.mark.asyncio
    async def test_create_run_rejects_active_run(
        self,
    ) -> None:
        """Verify create run rejects active run."""
        fake = settings.redis
        await fake.set("dp:active", "old")
        assert await create_run(settings.redis, "new", [], 0) is False

    @pytest.mark.asyncio
    async def test_read_failed_paths_filters_successes(
        self,
    ) -> None:
        """Verify read failed paths filters successes."""
        fake = settings.redis
        task = DumpTask(
            run_id="r1",
            table="p.app.t",
            bucket_path="s3://b/t",
            selection=AllSelection(),
        )

        await fake.hset(
            "dp:results:r1",
            mapping={
                task.task_id: DumpFailure(failed_path="s3://b/t").model_dump_json(),
                "success": DumpSuccess().model_dump_json(),
            },
        )

        assert await read_failed_paths(settings.redis, "r1") == {"s3://b/t"}
