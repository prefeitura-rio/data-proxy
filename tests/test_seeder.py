"""Tests for seeder dispatch policy."""

import logging
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from duckdb import connect as connect_duckdb
from faststream.exceptions import StopApplication
from redis.asyncio import Redis
from redis.typing import StreamRangeResponse

from dp.models import (
    PartitionedTable,
    PublishTask,
    SchemaConfig,
    SeedTask,
    SyncConfig,
    SyncPlan,
)
from dp.planning import expand_config
from dp.schema import initialize_schemas_for_plans
from dp.state import publication_exists
from dp.state_machines import seeder_claim
from dp.sync.seeder import (
    cleanup_consumers,
    dispatch_publication_tasks,
    seed_publication,
)
from tests.fixtures.types import Postgres

pytestmark = pytest.mark.usefixtures("test_settings", "metrics_disabled")


def stream_message() -> AsyncMock:
    """Return a stream message double that records acknowledgements."""
    return AsyncMock()


@pytest.fixture(autouse=True)
def allow_one_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let each test claim the seeder pod once."""
    seeder_claim.model.state = "unclaimed"
    seeder_claim.__init__(model=seeder_claim.model)


class TestSeeder:
    """Tests for seeder dispatch behavior."""

    @pytest.mark.asyncio
    async def test_dispatch_publication_tasks_publishes_one_task_per_schema(
        self,
        redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        GIVEN: multiple schema plans for one run.
        WHEN: dispatch_publication_tasks is called.
        THEN: it publishes one publication task per schema through one pipeline.
        """
        published: list[tuple[PublishTask, object]] = []

        class FakePublisher:
            async def publish(
                self, message: PublishTask, pipeline: object = None
            ) -> None:
                published.append((message, pipeline))

        def fake_publisher(**_: object) -> FakePublisher:
            return FakePublisher()

        monkeypatch.setattr("dp.sync.seeder.broker.publisher", fake_publisher)

        await dispatch_publication_tasks(
            SeedTask(run_id="r1"),
            [SyncPlan(schema_name="app"), SyncPlan(schema_name="other")],
        )

        assert [message.schema_name for message, _ in published] == ["app", "other"]
        assert all(message.run_id == "r1" for message, _ in published)
        assert published[0][1] is published[1][1]

    @pytest.mark.asyncio
    async def test_initialize_schemas_for_shared_writer_dsn(
        self,
        postgres: Postgres,
    ) -> None:
        """Schemas sharing one writer DSN are initialized in the session database."""
        schema = postgres.namespace.schema
        other_schema = f"{schema}_two"
        plans = [
            SyncPlan(schema_name=schema),
            SyncPlan(schema_name=other_schema),
        ]
        await initialize_schemas_for_plans(
            plans,
            lambda _: postgres.dsn,
            {
                schema: SchemaConfig(tables=[]),
                other_schema: SchemaConfig(tables=[]),
            },
        )
        cursor = await postgres.connection.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname IN (%s, %s) ORDER BY nspname",
            (schema, other_schema),
        )
        assert await cursor.fetchall() == [(schema,), (other_schema,)]
        await postgres.connection.execute(
            f'DROP SCHEMA "{other_schema}" CASCADE'.encode()
        )
        await postgres.connection.commit()

    def test_dispatch_exists_matches_run_id_in_payload(
        self,
    ) -> None:
        """
        GIVEN: stream entries with a run_id in the payload.
        WHEN: publication_exists is called with a matching run id.
        THEN: it returns True for a match and False for a mismatch.
        """
        assert publication_exists([(b"1-0", {b"__data__": b'"run_id":"r1"'})], "r1")
        assert not publication_exists([(b"1-0", {b"__data__": b'"run_id":"r1"'})], "r2")

    def test_publication_exists_skips_entries_without_a_data_mapping(
        self,
    ) -> None:
        """
        GIVEN: a stream entry whose payload is not a mapping.
        WHEN: publication_exists is called.
        THEN: it ignores the entry and returns False.
        """
        entries = cast(StreamRangeResponse, cast(object, [(b"1-0", "not-a-mapping")]))
        assert publication_exists(entries, "r1") is False

    @pytest.mark.asyncio
    async def test_seeder_groups_schemas_and_dispatches(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: stored plans for multiple schemas.
        WHEN: seed_publication is called.
        THEN: it groups schemas and dispatches each to handle_publish_task.
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
            patch(
                "dp.schema.AsyncConnection.connect",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            patch("dp.schema.initialize_schemas", new_callable=AsyncMock),
            patch(
                "dp.sync.publisher.apply_sync_plan",
                new_callable=AsyncMock,
                return_value=MagicMock(
                    plan=SyncPlan(schema_name="app"),
                    published_tables=set(),
                ),
            ),
            patch("dp.utils.complete_schema", new_callable=AsyncMock),
            patch(
                "dp.sync.seeder.dispatch_publication_tasks", new_callable=AsyncMock
            ) as dispatch,
            pytest.raises(StopApplication),
        ):
            await seed_publication(
                SeedTask(run_id="r1"), logging.getLogger("test"), stream_message()
            )

        dispatch.assert_awaited_once()
        assert dispatch.await_args is not None
        _, plans = dispatch.await_args.args
        assert {plan.schema_name for plan in plans} == {"app", "other"}

    @pytest.mark.asyncio
    async def test_seeder_acknowledges_and_stops_after_dispatch(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: one seed task for a run.
        WHEN: seed_publication is called.
        THEN: it acknowledges the message and stops the application.
        """
        sync_config_path.write_text(
            SyncConfig(schemas={"app": SchemaConfig()}).model_dump_json()
        )
        await redis.hset(
            "dp:plans:r1", "app", SyncPlan(schema_name="app").model_dump_json()
        )
        message = stream_message()
        with (
            patch(
                "dp.schema.AsyncConnection.connect",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            patch("dp.schema.initialize_schemas", new_callable=AsyncMock),
            patch("dp.sync.seeder.dispatch_publication_tasks", new_callable=AsyncMock),
            pytest.raises(StopApplication),
        ):
            await seed_publication(
                SeedTask(run_id="r1"), logging.getLogger("test"), message
            )

        message.ack.assert_awaited_once()
        assert message.ack.await_args is not None
        assert message.ack.await_args.kwargs["group"] == "seeders"

    @pytest.mark.asyncio
    async def test_second_seed_message_stays_pending(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a pod that already claimed one seed task.
        WHEN: seed_publication is called again.
        THEN: it dispatches nothing, acknowledges nothing, and stops.
        """
        message = stream_message()
        seeder_claim.send("claim")
        with (
            patch("dp.schema.initialize_schemas", new_callable=AsyncMock) as initialize,
            pytest.raises(StopApplication),
        ):
            await seed_publication(
                SeedTask(run_id="r1"), logging.getLogger("test"), message
            )

        initialize.assert_not_called()
        message.ack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_dispatch_is_not_acknowledged(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a schema initialization that raises.
        WHEN: seed_publication is called.
        THEN: the message stays pending for the reclaim subscription.
        """
        sync_config_path.write_text(
            SyncConfig(schemas={"app": SchemaConfig()}).model_dump_json()
        )
        await redis.hset(
            "dp:plans:r1", "app", SyncPlan(schema_name="app").model_dump_json()
        )
        message = stream_message()
        with (
            patch(
                "dp.schema.AsyncConnection.connect",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            patch(
                "dp.schema.initialize_schemas",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            await seed_publication(
                SeedTask(run_id="r1"), logging.getLogger("test"), message
            )

        message.ack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_seeder_cleanup_removes_each_consumer_once(
        self, redis: Redis
    ) -> None:
        """
        GIVEN: two seeder consumers.
        WHEN: cleanup_consumers runs.
        THEN: each consumer is cleaned up exactly once.
        """
        with (
            patch("dp.utils.cleanup_consumer", new_callable=AsyncMock) as cleanup,
        ):
            await cleanup_consumers()
        assert cleanup.await_count == 2

    @pytest.mark.asyncio
    async def test_expand_config_skips_partitioned(
        self,
    ) -> None:
        """
        GIVEN: a config with only partitioned tables.
        WHEN: expand_config is called.
        THEN: it returns no tasks.
        """
        db = connect_duckdb(":memory:")
        assert await expand_config([PartitionedTable(name="p.d.t")], "b", "r", db) == []

    def test_dispatch_exists_returns_false_for_empty_stream(
        self,
    ) -> None:
        """
        GIVEN: an empty stream.
        WHEN: publication_exists is called.
        THEN: it returns False.
        """
        assert publication_exists([], "r1") is False

    @pytest.mark.asyncio
    async def test_seeder_skips_existing_dispatch(
        self, sync_config_path: Path, redis: Redis
    ) -> None:
        """
        GIVEN: an existing dispatch for the run.
        WHEN: seed_publication is called.
        THEN: the seeder skips dispatch, acknowledges the message, and stops.
        """
        message = stream_message()
        with (
            patch("dp.sync.seeder.publication_exists", return_value=True),
            pytest.raises(StopApplication),
        ):
            await seed_publication(
                SeedTask(run_id="r1"), logging.getLogger("test"), message
            )

        message.ack.assert_awaited_once()
