"""Tests for current publisher subscriptions."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from duckdb import connect
from psycopg import Connection
from redis.asyncio import Redis

from dp.models import (
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    PublicationResult,
    PublishTask,
    RangeSelection,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
)
from dp.sync.publisher import (
    cleanup_publisher_consumers,
    publish_plan,
    publish_schema,
    publisher,
    subs,
)
from dp.sync.seeder import broker as seeder_broker

pytestmark = pytest.mark.usefixtures("test_settings")


class TestPublisher:
    """Tests for publisher subscriber behavior."""

    def test_publisher_has_new_and_stale_subscriptions(
        self,
    ) -> None:
        """
        GIVEN: the publisher subscriber configuration.
        WHEN: the subscriptions are inspected.
        THEN: new has no min_idle_time and stale does.
        """
        assert subs["new"].min_idle_time is None
        assert subs["stale"].min_idle_time is not None

    @pytest.mark.asyncio
    async def test_missing_publish_plan_exits_application(
        self, sync_config_path: Path, redis: Redis
    ) -> None:
        """
        GIVEN: an active run with no remaining publish plan.
        WHEN: publish_schema is called.
        THEN: the publisher application exits.
        """
        await redis.set("dp:active", "r1")
        with (
            patch("dp.sync.publisher.psycopg.connect", return_value=MagicMock()),
            patch("dp.sync.publisher.reload_postgrest"),
            patch.object(publisher, "exit") as exit_app,
        ):
            await publish_schema(PublishTask(run_id="r1", schema_name="app"))
        exit_app.assert_called_once()

    def test_publish_plan_wraps_connections(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a writer DSN, config, and plan.
        WHEN: publish_plan is called.
        THEN: it wraps PostgreSQL and DuckDB connections and delegates to apply_sync_plan.
        """
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.t")])}
        )
        plan = SyncPlan(schema_name="app")
        with (
            patch("dp.sync.publisher.psycopg.connect", return_value=postgres),
            patch("dp.sync.publisher.connect", return_value=connect(":memory:")),
            patch(
                "dp.sync.publisher.apply_sync_plan",
                return_value=PublicationResult(plan=plan, published_tables=set()),
            ) as apply,
        ):
            result = publish_plan("postgresql://writer", config, plan, set())
        assert result.published_tables == set()
        apply.assert_called_once()

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
            SyncConfig(
                schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.t")])}
            ).model_dump_json()
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
            patch("dp.sync.publisher.publish_plan", return_value=result),
            patch(
                "dp.sync.publisher.complete_schema",
                new_callable=AsyncMock,
                return_value=1,
            ),
        ):
            await seeder_broker.publish(
                PublishTask(run_id="r1", schema_name="app"), stream="dp:publish"
            )
        assert publish_schema.mock.call_count == 2

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
            SyncConfig(
                schemas={"app": SchemaConfig(tables=[PartitionedTable(name="p.app.t")])}
            ).model_dump_json()
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
                "dp.sync.publisher.complete_schema",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("dp.sync.publisher.psycopg.connect", return_value=MagicMock()),
            patch("dp.sync.publisher.reload_postgrest"),
            patch.object(publisher, "exit"),
        ):
            await publish_schema(PublishTask(run_id="r1", schema_name="app"))

    @pytest.mark.asyncio
    async def test_publisher_cleanup_removes_each_consumer_once(
        self, redis: Redis
    ) -> None:
        """
        GIVEN: two publisher consumers.
        WHEN: cleanup_publisher_consumers runs.
        THEN: each consumer is cleaned up exactly once.
        """
        with (
            patch(
                "dp.sync.publisher.cleanup_consumer", new_callable=AsyncMock
            ) as cleanup,
        ):
            await cleanup_publisher_consumers()
        assert cleanup.await_count == 2
