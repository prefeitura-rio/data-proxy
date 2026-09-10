"""Tests for current publisher subscriptions."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg import Connection
from redis.asyncio import Redis

from dp.loading import publish_plan
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
from dp.sync.publisher import (
    cleanup_consumers,
    publish_schema,
    publisher,
)
from dp.sync.seeder import broker as seeder_broker
from tests.conftest import PostgresTestNamespace
from tests.helpers import sync_config

pytestmark = pytest.mark.usefixtures("test_settings", "mock_push_to_gateway")


class TestPublisherSubscriber:
    """Tests for publisher subscriber behavior."""

    @pytest.mark.asyncio
    async def test_missing_publish_plan_does_not_exit_application(
        self, sync_config_path: Path, redis: Redis
    ) -> None:
        """
        GIVEN: an active run with no remaining publish plan.
        WHEN: publish_schema is called.
        THEN: the publisher application does not exit so it can process the next message.
        """
        await redis.set("dp:active", "r1")
        with (
            patch("dp.utils.psycopg.connect", return_value=MagicMock()),
            patch("dp.utils.reload_postgrest"),
            patch.object(publisher, "exit") as exit_app,
        ):
            await publish_schema(
                PublishTask(run_id="r1", schema_name="app"), logging.getLogger("test")
            )
        exit_app.assert_not_called()


class TestPublishPlan:
    """Tests for direct publication service behavior."""

    def test_publish_plan_wraps_connections(
        self,
    ) -> None:
        """
        GIVEN: a writer DSN, config, and plan.
        WHEN: publish_plan is called.
        THEN: it wraps a PostgreSQL connection and delegates to apply_sync_plan.
        """
        config = sync_config([FullTable(name="p.app.t")])
        plan = SyncPlan(schema_name="app")
        with (
            patch(
                "dp.loading.psycopg.connect", return_value=MagicMock(spec=Connection)
            ),
            patch(
                "dp.loading.apply_sync_plan",
                return_value=PublicationResult(plan=plan, published_tables=set()),
            ) as apply,
        ):
            result = publish_plan("postgresql://writer", config, plan, set())
        assert result.published_tables == set()
        apply.assert_called_once()

    def test_publish_plan_uses_real_writer_connection(
        self,
        postgres: Connection[tuple[object, ...]],
        postgres_dsn: str,
        namespace: PostgresTestNamespace,
    ) -> None:
        """A writer DSN opens the cloned database and initializes its schema."""
        config = sync_config(
            [FullTable(name=f"p.{namespace.schema}.t")],
            schema_name=namespace.schema,
        )
        result = publish_plan(
            postgres_dsn, config, SyncPlan(schema_name=namespace.schema), set()
        )
        assert result.published_tables == set()
        assert postgres.execute(
            "SELECT to_regnamespace(%s)", (namespace.schema,)
        ).fetchone() == (namespace.schema,)


class TestPublishSchema:
    """Tests for publish-schema subscriber behavior."""

    @pytest.mark.asyncio
    async def test_publish_schema_publishes_and_keeps_remaining_plan(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: a stored plan with remaining schemas after publication.
        WHEN: publish_schema is called.
        THEN: it publishes the schema and keeps the remaining plan.
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
            patch.object(publisher, "exit"),
            patch("dp.sync.publisher.flush_cache", new_callable=AsyncMock),
            patch("dp.sync.publisher.publish_plan", return_value=result),
            patch(
                "dp.utils.complete_schema",
                new_callable=AsyncMock,
                return_value=1,
            ),
        ):
            await seeder_broker.publish(
                PublishTask(run_id="r1", schema_name="app"), stream="dp:publish"
            )
        assert publish_schema.mock.call_count == 2

    @pytest.mark.asyncio
    async def test_publish_schema_flushes_configured_fallback_cache(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sync_config_path: Path,
    ) -> None:
        """Enabled fallback flushes the configured response cache database."""
        sync_config_path.write_text(
            sync_config([FullTable(name="p.app.t")]).model_dump_json()
        )
        plan = SyncPlan(schema_name="app")
        monkeypatch.setattr(settings, "FALLBACK_ENABLED", True)
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
                "dp.sync.publisher.publish_plan",
                return_value=PublicationResult(plan=plan, published_tables=set()),
            ),
            patch("dp.utils.complete_schema", new_callable=AsyncMock, return_value=1),
            patch(
                "dp.sync.publisher.flush_cache", new_callable=AsyncMock
            ) as flush_cache,
        ):
            await publish_schema(
                PublishTask(run_id="r1", schema_name="app"), logging.getLogger("test")
            )

        flush_cache.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_publisher_commits_partition_state_and_cleans_last_plan(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a published partitioned table with zero remaining schemas.
        WHEN: publish_schema is called.
        THEN: it commits the partition state and cleans the last plan.
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
                "dp.sync.publisher.publish_plan",
                return_value=MagicMock(plan=plan, published_tables={"p.app.t"}),
            ),
            patch(
                "dp.utils.complete_schema",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("dp.utils.psycopg.connect", return_value=MagicMock()),
            patch("dp.utils.reload_postgrest"),
            patch.object(publisher, "exit"),
            patch("dp.sync.publisher.flush_cache", new_callable=AsyncMock),
        ):
            await publish_schema(
                PublishTask(run_id="r1", schema_name="app"), logging.getLogger("test")
            )

    @pytest.mark.asyncio
    async def test_publish_schema_continues_after_successful_publish(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a stored plan with remaining schemas after publication.
        WHEN: publish_schema is called.
        THEN: the publisher application does not exit so it can process the next message.
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
                "dp.sync.publisher.publish_plan",
                return_value=MagicMock(plan=plan, published_tables={"p.app.t"}),
            ),
            patch(
                "dp.utils.complete_schema",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("dp.utils.psycopg.connect", return_value=MagicMock()),
            patch("dp.utils.reload_postgrest"),
            patch.object(publisher, "exit") as exit_app,
        ):
            await publish_schema(
                PublishTask(run_id="r1", schema_name="app"), logging.getLogger("test")
            )

        exit_app.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_schema_increments_failure_counter_for_unpublished_tables(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a stored plan where no tables were published.
        WHEN: publish_schema is called.
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
            patch.object(publisher, "exit"),
            patch("dp.sync.publisher.flush_cache", new_callable=AsyncMock),
            patch("dp.sync.publisher.publish_plan", return_value=result),
            patch(
                "dp.utils.complete_schema",
                new_callable=AsyncMock,
                return_value=1,
            ),
        ):
            await publish_schema(
                PublishTask(run_id="r1", schema_name="app"), logging.getLogger("test")
            )


class TestPublisherCleanup:
    """Tests for publisher consumer cleanup."""

    @pytest.mark.asyncio
    async def test_publisher_cleanup_removes_each_consumer_once(
        self, redis: Redis
    ) -> None:
        """
        GIVEN: two publisher consumers.
        WHEN: cleanup_consumers runs.
        THEN: each consumer is cleaned up exactly once.
        """
        with (
            patch(
                "dp.sync.publisher.cleanup_consumer", new_callable=AsyncMock
            ) as cleanup,
        ):
            await cleanup_consumers()

        assert cleanup.await_count == 2
