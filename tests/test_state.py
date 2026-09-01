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
    async def test_create_run_stores_and_reads_schema_plans(
        self,
    ) -> None:
        """
        GIVEN: a schema plan for a new run.
        WHEN: create_run and read_sync_plan are called.
        THEN: the plan is stored and read back from the run hash.
        """
        plan = SyncPlan(
            schema_name="app", signatures={"p.d.t": "s"}, paths={"p.d.t": ["s3://b/t"]}
        )
        assert await create_run(settings.redis, "r1", [plan], 1)
        assert await read_sync_plan(settings.redis, "r1", "app") == plan

    @pytest.mark.asyncio
    async def test_complete_dump_is_idempotent_for_duplicate_results(
        self,
    ) -> None:
        """
        GIVEN: a dump failure followed by a duplicate dump success.
        WHEN: complete_dump is called twice.
        THEN: the second call is a no-op and returns None.
        """
        await settings.redis.set("dp:remaining:r1", "1")
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
    async def test_complete_schema_removes_plan_and_counts_remaining(
        self,
    ) -> None:
        """
        GIVEN: a run with multiple schema plans.
        WHEN: complete_schema is called for one schema.
        THEN: it removes that plan and returns the remaining count.
        """
        plans_key = "dp:plans:r1"
        await settings.redis.hset(plans_key, mapping={"app": "{}", "other": "{}"})
        states = {"p.d.t": TableState(strategy=Strategy.FULL, signature="s")}
        assert await complete_schema(settings.redis, "r1", "app", states) == 1
        assert await settings.redis.hexists(plans_key, "app") is False
        assert await complete_schema(settings.redis, "r1", "app", states) is None

    @pytest.mark.asyncio
    async def test_state_readers_return_none_for_missing_values(
        self,
    ) -> None:
        """
        GIVEN: no stored state values.
        WHEN: state readers are called.
        THEN: they all return None.
        """
        assert await read_table_state(settings.redis, "p.d.t") is None
        assert await read_table_signature(settings.redis, "p.d.t") is None
        assert await read_partition_manifest(settings.redis, "p.d.t") is None
        assert await read_active_run(settings.redis) is None
        assert await read_remaining(settings.redis, "r") is None

    @pytest.mark.asyncio
    async def test_create_consumer_group_ignores_busy_group_error(
        self,
    ) -> None:
        """
        GIVEN: a consumer group that already exists.
        WHEN: create_consumer_group is called twice.
        THEN: the busy-group error is ignored.
        """
        await create_consumer_group(settings.redis, "s", "g")
        await create_consumer_group(settings.redis, "s", "g")

    @pytest.mark.asyncio
    async def test_complete_dump_rejects_missing_and_invalid_remaining_counter(
        self,
    ) -> None:
        """
        GIVEN: a dump success with no remaining counter and then an invalid counter.
        WHEN: complete_dump is called.
        THEN: it raises RuntimeError for both the missing and invalid counter.
        """
        task = DumpTask(
            run_id="r", table="p.d.t", bucket_path="s3://b", selection=AllSelection()
        )
        with pytest.raises(RuntimeError, match="Remaining task count"):
            await complete_dump((settings.redis), task, DumpSuccess())
        await settings.redis.set("dp:remaining:r", "0")
        with pytest.raises(RuntimeError, match="Invalid remaining"):
            await complete_dump(settings.redis, task, DumpSuccess())

    @pytest.mark.asyncio
    async def test_state_readers_return_stored_values_and_groups_are_created(
        self,
    ) -> None:
        """
        GIVEN: stored run state, table state, and schema plans.
        WHEN: state readers and ensure_groups are called.
        THEN: they return the stored values and groups are created without error.
        """
        await settings.redis.set("dp:active", "r1")
        await settings.redis.set("dp:remaining:r1", "2")
        await settings.redis.set(
            "dp:state:p.d.t",
            TableState(strategy=Strategy.FULL, signature="s").model_dump_json(),
        )
        await settings.redis.hset(
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
    async def test_cleanup_run_and_consumer_remove_all_run_keys(
        self,
    ) -> None:
        """
        GIVEN: an active run with zero remaining and an idle consumer.
        WHEN: cleanup_consumer and cleanup_run are called.
        THEN: all run keys are removed from Redis.
        """
        await settings.redis.mset({"dp:active": "r1", "dp:remaining:r1": "0"})
        await create_consumer_group(settings.redis, "stream", "group")
        await cleanup_consumer(settings.redis, "stream", "group", "consumer")
        await cleanup_run(settings.redis, "r1")
        assert (
            await settings.redis.exists(
                "dp:active", "dp:plans:r1", "dp:remaining:r1", "dp:results:r1"
            )
            == 0
        )

    @pytest.mark.asyncio
    async def test_cleanup_consumer_preserves_pending_messages(
        self,
    ) -> None:
        """
        GIVEN: a consumer with pending messages.
        WHEN: cleanup_consumer is called.
        THEN: the pending messages are kept.
        """
        stream = "pending-stream"
        group = "pending-group"
        consumer = "pending-consumer"
        await settings.redis.xadd(stream, {"payload": "value"})
        await settings.redis.xgroup_create(stream, group, id="0")
        await settings.redis.xreadgroup(group, consumer, {stream: ">"})

        await cleanup_consumer(settings.redis, stream, group, consumer)

        assert (
            await settings.redis.xpending_range(stream, group, "-", "+", 10, consumer)
            != []
        )

    @pytest.mark.asyncio
    async def test_read_partition_manifest_returns_manifest_for_partitioned_table(
        self,
    ) -> None:
        """
        GIVEN: a partitioned table state with an empty partition manifest.
        WHEN: read_partition_manifest is called.
        THEN: it returns the manifest.
        """
        await settings.redis.set(
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
        """
        GIVEN: an existing active run.
        WHEN: create_run is called for a new run.
        THEN: it returns False.
        """
        await settings.redis.set("dp:active", "old")
        assert await create_run(settings.redis, "new", [], 0) is False

    @pytest.mark.asyncio
    async def test_read_failed_paths_returns_only_failed_paths(
        self,
    ) -> None:
        """
        GIVEN: dump results with both failures and successes.
        WHEN: read_failed_paths is called.
        THEN: it returns only the failed paths.
        """
        task = DumpTask(
            run_id="r1",
            table="p.app.t",
            bucket_path="s3://b/t",
            selection=AllSelection(),
        )

        await settings.redis.hset(
            "dp:results:r1",
            mapping={
                task.task_id: DumpFailure(failed_path="s3://b/t").model_dump_json(),
                "success": DumpSuccess().model_dump_json(),
            },
        )

        assert await read_failed_paths(settings.redis, "r1") == {"s3://b/t"}
