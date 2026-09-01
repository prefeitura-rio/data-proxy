"""Tests for seeder dispatch policy."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from duckdb import connect
from redis.asyncio import Redis

from dp.models import (
    PartitionedTable,
    SchemaConfig,
    SeedTask,
    SyncConfig,
    SyncPlan,
)
from dp.planning import expand_config
from dp.sync.publisher import publish_schema, publisher
from dp.sync.seeder import cleanup_seeder_consumers, dispatch_exists, seed_sync, seeder

pytestmark = pytest.mark.usefixtures("test_settings")


class TestSeeder:
    """Tests for seeder dispatch behavior."""

    def test_dispatch_exists_matches_run_id_in_payload(
        self,
    ) -> None:
        """
        GIVEN: stream entries with a run_id in the payload.
        WHEN: dispatch_exists is called with a matching run id.
        THEN: it returns True for a match and False for a mismatch.
        """
        assert dispatch_exists([(b"1-0", {b"__data__": b'"run_id":"r1"'})], "r1")
        assert not dispatch_exists([(b"1-0", {b"__data__": b'"run_id":"r1"'})], "r2")

    @pytest.mark.asyncio
    async def test_seeder_groups_schemas_and_dispatches(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: stored plans for multiple schemas.
        WHEN: seed_sync is called.
        THEN: it groups schemas and dispatches each to publish_schema.
        """
        sync_config_path.write_text(
            SyncConfig(
                schemas={"app": SchemaConfig(), "other": SchemaConfig()}
            ).model_dump_json()
        )
        plans_key = "dp:plans:r1"
        await redis.hset(
            plans_key,
            mapping={
                "app": SyncPlan(schema_name="app").model_dump_json(),
                "other": SyncPlan(schema_name="other").model_dump_json(),
            },
        )
        with (
            patch("dp.sync.seeder.psycopg.connect", return_value=MagicMock()),
            patch("dp.sync.seeder.initialize_schemas"),
            patch(
                "dp.sync.publisher.asyncify",
                return_value=AsyncMock(
                    return_value=MagicMock(
                        plan=SyncPlan(schema_name="app"),
                        published_tables=set(),
                    )
                ),
            ),
            patch("dp.sync.publisher.complete_schema", new_callable=AsyncMock),
            patch.object(publisher, "exit"),
            patch.object(seeder, "exit"),
        ):
            await seed_sync(SeedTask(run_id="r1"))
        assert publish_schema.mock.call_count == 4

    @pytest.mark.asyncio
    async def test_seeder_cleanup_removes_each_consumer_once(
        self, redis: Redis
    ) -> None:
        """
        GIVEN: two seeder consumers.
        WHEN: cleanup_seeder_consumers runs.
        THEN: each consumer is cleaned up exactly once.
        """
        with (
            patch("dp.sync.seeder.cleanup_consumer", new_callable=AsyncMock) as cleanup,
        ):
            await cleanup_seeder_consumers()
        assert cleanup.await_count == 2

    def test_expand_config_skips_partitioned(
        self,
    ) -> None:
        """
        GIVEN: a config with only partitioned tables.
        WHEN: expand_config is called.
        THEN: it returns no tasks.
        """
        db = connect(":memory:")
        assert expand_config([PartitionedTable(name="p.d.t")], "b", "r", db) == []

    def test_dispatch_exists_returns_false_for_empty_stream(
        self,
    ) -> None:
        """
        GIVEN: an empty stream.
        WHEN: dispatch_exists is called.
        THEN: it returns False.
        """
        assert dispatch_exists([], "r1") is False

    @pytest.mark.asyncio
    async def test_seeder_skips_existing_dispatch(
        self, sync_config_path: Path, redis: Redis
    ) -> None:
        """
        GIVEN: an existing dispatch for the run.
        WHEN: seed_sync is called.
        THEN: the seeder skips dispatch and exits.
        """
        with (
            patch("dp.sync.seeder.dispatch_exists", return_value=True),
            patch.object(seeder, "exit") as exit_app,
        ):
            await seed_sync(SeedTask(run_id="r1"))
        exit_app.assert_called_once()
