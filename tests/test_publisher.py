"""Tests for current publisher subscriptions."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from faststream.exceptions import StopApplication
from faststream.redis import StreamSub
from psycopg import AsyncConnection
from redis.asyncio import Redis

from dp.models import (
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    PublicationResult,
    PublishTask,
    RangeSelection,
    SyncPlan,
)
from dp.settings import settings
from dp.state_machines import publisher_claim
from dp.sync.publisher import (
    handle_publish_task,
    remove_idle_consumers,
)
from dp.sync.seeder import broker as seeder_broker
from dp.utils import remove_idle_consumers as remove_idle_consumers_utils
from tests.helpers import sync_config

pytestmark = pytest.mark.usefixtures("test_settings", "metrics_disabled")


def stream_message() -> AsyncMock:
    """Return a stream message double that records acknowledgements."""
    return AsyncMock()


@pytest.fixture(autouse=True)
def allow_one_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let each test claim the publisher pod once."""
    publisher_claim.model.state = "unclaimed"
    publisher_claim.__init__(model=publisher_claim.model)
    monkeypatch.setattr(
        AsyncConnection,
        "connect",
        AsyncMock(return_value=AsyncMock()),
    )
    monkeypatch.setattr(
        "dp.sync.publisher.refresh_postgrest",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "dp.sync.publisher.current_wal_lsn",
        AsyncMock(return_value="0/1"),
    )
    monkeypatch.setattr(
        "dp.sync.publisher.wait_for_replica_replay",
        AsyncMock(),
    )


class TestPublisherSubscriber:
    """Tests for publisher subscriber behavior."""

    @pytest.mark.asyncio
    async def test_missing_apply_sync_plan_acks_and_stops_the_application(
        self, sync_config_path: Path, redis: Redis
    ) -> None:
        """
        GIVEN: an active run with no remaining publish plan.
        WHEN: handle_publish_task is called.
        THEN: the message is acknowledged and the application stops.
        """
        await redis.set("dp:active", "r1")
        message = stream_message()
        with (
            patch("dp.utils.revoke_anonymous_access"),
            pytest.raises(StopApplication),
        ):
            await handle_publish_task(
                PublishTask(run_id="r1", schema_name="app"),
                logging.getLogger("test"),
                message,
            )

        message.ack.assert_awaited_once()
        assert message.ack.await_args is not None
        assert message.ack.await_args.kwargs["group"] == "publishers"


class TestPublishSchema:
    """Tests for publish-schema subscriber behavior."""

    @pytest.mark.asyncio
    async def test_handle_task_publishes_and_keeps_remaining_plan(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: a stored plan with remaining schemas after publication.
        WHEN: handle_publish_task is called.
        THEN: it publishes the schema, keeps the remaining plan, and keeps the bucket.
        """
        sync_config_path.write_text(
            sync_config([FullTable(name="p.app.t")]).model_dump_json()
        )
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.t": "sig"},
            paths={"p.app.t": ["s3://b/t"]},
        )
        await redis.hset("dp:plans:r1", "app", plan.model_dump_json())
        result = PublicationResult(plan=plan, published_tables={"p.app.t"})
        with (
            patch("dp.sync.publisher.clear_response_cache", new_callable=AsyncMock),
            patch(
                "dp.sync.publisher.apply_sync_plan",
                new_callable=AsyncMock,
                return_value=result,
            ) as apply,
            patch(
                "dp.utils.complete_schema",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "faststream.redis.message.RedisStreamMessage.ack",
                new_callable=AsyncMock,
            ),
            patch("dp.utils.clear_s3_bucket", new_callable=AsyncMock) as empty,
            pytest.raises(StopApplication),
        ):
            await seeder_broker.publish(
                PublishTask(run_id="r1", schema_name="app"), stream="dp:publish"
            )
        apply.assert_awaited_once()
        empty.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_task_flushes_configured_fallback_cache(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sync_config_path: Path,
    ) -> None:
        """Enabled fallback flushes the configured response cache database."""
        sync_config_path.write_text(
            sync_config([FullTable(name="p.app.t")]).model_dump_json()
        )
        plan = SyncPlan(schema_name="app")
        monkeypatch.setattr(settings, "FALLBACK_CACHE_REDIS_DB", 7)
        with (
            patch(
                "dp.sync.publisher.read_sync_plan",
                new_callable=AsyncMock,
                return_value=plan,
            ),
            patch(
                "dp.sync.publisher.read_failed_paths",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "dp.sync.publisher.apply_sync_plan",
                return_value=PublicationResult(plan=plan, published_tables=set()),
            ),
            patch("dp.utils.complete_schema", new_callable=AsyncMock, return_value=1),
            patch(
                "dp.sync.publisher.clear_response_cache", new_callable=AsyncMock
            ) as clear_response_cache,
            pytest.raises(StopApplication),
        ):
            await handle_publish_task(
                PublishTask(run_id="r1", schema_name="app"),
                logging.getLogger("test"),
                stream_message(),
            )

        clear_response_cache.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_publisher_commits_partition_state_and_cleans_last_plan(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a published partitioned table with zero remaining schemas.
        WHEN: handle_publish_task is called.
        THEN: it commits the partition state, cleans the last plan, and empties the bucket.
        """
        sync_config_path.write_text(
            sync_config([PartitionedTable(name="p.app.t")]).model_dump_json()
        )
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.t": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={
                        "1": PhysicalPartition(
                            partition_id="1",
                            signature="s",
                            selection=RangeSelection(
                                partition_id="1", column="id", lower=1, upper=2
                            ),
                        )
                    },
                    changed_paths={"1": "s3://b"},
                    removed_partitions={},
                )
            },
        )
        with (
            patch(
                "dp.sync.publisher.read_sync_plan",
                new_callable=AsyncMock,
                return_value=plan,
            ),
            patch(
                "dp.sync.publisher.read_failed_paths",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "dp.sync.publisher.apply_sync_plan",
                return_value=MagicMock(plan=plan, published_tables={"p.app.t"}),
            ),
            patch(
                "dp.utils.complete_schema",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("dp.utils.revoke_anonymous_access"),
            patch("dp.sync.publisher.clear_response_cache", new_callable=AsyncMock),
            patch("dp.utils.clear_s3_bucket", new_callable=AsyncMock) as empty,
            pytest.raises(StopApplication),
        ):
            await handle_publish_task(
                PublishTask(run_id="r1", schema_name="app"),
                logging.getLogger("test"),
                stream_message(),
            )

        empty.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_task_stops_after_successful_publish(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a stored plan with remaining schemas after publication.
        WHEN: handle_publish_task is called.
        THEN: it acknowledges the message and stops the application.
        """
        sync_config_path.write_text(
            sync_config([PartitionedTable(name="p.app.t")]).model_dump_json()
        )

        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.t": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={
                        "1": PhysicalPartition(
                            partition_id="1",
                            signature="s",
                            selection=RangeSelection(
                                partition_id="1", column="id", lower=1, upper=2
                            ),
                        )
                    },
                    changed_paths={"1": "s3://b"},
                    removed_partitions={},
                )
            },
        )

        message = stream_message()
        with (
            patch(
                "dp.sync.publisher.read_sync_plan",
                new_callable=AsyncMock,
                return_value=plan,
            ),
            patch(
                "dp.sync.publisher.read_failed_paths",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "dp.sync.publisher.apply_sync_plan",
                return_value=MagicMock(plan=plan, published_tables={"p.app.t"}),
            ),
            patch(
                "dp.utils.complete_schema",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("dp.utils.revoke_anonymous_access"),
            patch("dp.utils.clear_s3_bucket", new_callable=AsyncMock),
            patch("dp.sync.publisher.clear_response_cache", new_callable=AsyncMock),
            pytest.raises(StopApplication),
        ):
            await handle_publish_task(
                PublishTask(run_id="r1", schema_name="app"),
                logging.getLogger("test"),
                message,
            )

        message.ack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_second_message_stays_pending_without_publication(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a pod that already claimed one schema.
        WHEN: handle_publish_task is called again.
        THEN: it publishes nothing, acknowledges nothing, and stops.
        """
        sync_config_path.write_text(
            sync_config([FullTable(name="p.app.t")]).model_dump_json()
        )
        message = stream_message()
        publisher_claim.send("claim")
        with (
            patch(
                "dp.sync.publisher.apply_sync_plan", new_callable=AsyncMock
            ) as publish,
            pytest.raises(StopApplication),
        ):
            await handle_publish_task(
                PublishTask(run_id="r1", schema_name="app"),
                logging.getLogger("test"),
                message,
            )

        publish.assert_not_called()
        message.ack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_publication_is_not_acknowledged(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a publication that raises.
        WHEN: handle_publish_task is called.
        THEN: the message stays pending for the reclaim subscription.
        """
        sync_config_path.write_text(
            sync_config([FullTable(name="p.app.t")]).model_dump_json()
        )
        plan = SyncPlan(schema_name="app")
        message = stream_message()
        with (
            patch(
                "dp.sync.publisher.read_sync_plan",
                new_callable=AsyncMock,
                return_value=plan,
            ),
            patch(
                "dp.sync.publisher.read_failed_paths",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "dp.sync.publisher.apply_sync_plan",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            await handle_publish_task(
                PublishTask(run_id="r1", schema_name="app"),
                logging.getLogger("test"),
                message,
            )

        message.ack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_task_increments_failure_counter_for_unpublished_tables(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a stored plan where no tables were published.
        WHEN: handle_publish_task is called.
        THEN: the failure counter is incremented for unpublished tables.
        """
        sync_config_path.write_text(
            sync_config([FullTable(name="p.app.t")]).model_dump_json()
        )

        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.t": "sig"},
            paths={"p.app.t": ["s3://b/t"]},
        )

        await redis.hset("dp:plans:r1", "app", plan.model_dump_json())
        result = PublicationResult(plan=plan, published_tables=set())

        with (
            patch("dp.sync.publisher.clear_response_cache", new_callable=AsyncMock),
            patch(
                "dp.sync.publisher.apply_sync_plan",
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch(
                "dp.utils.complete_schema",
                new_callable=AsyncMock,
                return_value=1,
            ),
            pytest.raises(StopApplication),
        ):
            await handle_publish_task(
                PublishTask(run_id="r1", schema_name="app"),
                logging.getLogger("test"),
                stream_message(),
            )


class TestPublisherCleanup:
    """Tests for publisher consumer cleanup."""

    @pytest.mark.asyncio
    async def test_remove_idle_consumers_skips_consumerless_subscriptions(
        self, redis: Redis
    ) -> None:
        """
        GIVEN: a subscription without a consumer.
        WHEN: remove_idle_consumers runs.
        THEN: it does not call cleanup for that subscription.
        """
        subs = {"new": StreamSub("s")}
        with patch("dp.utils.cleanup_consumer", new_callable=AsyncMock) as cleanup:
            await remove_idle_consumers_utils(redis, "stream", "group", subs)

        cleanup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publisher_cleanup_removes_each_consumer_once(
        self, redis: Redis
    ) -> None:
        """
        GIVEN: two publisher consumers.
        WHEN: remove_idle_consumers runs.
        THEN: each consumer is cleaned up exactly once.
        """
        with (
            patch("dp.utils.cleanup_consumer", new_callable=AsyncMock) as cleanup,
        ):
            await remove_idle_consumers()

        assert cleanup.await_count == 2
