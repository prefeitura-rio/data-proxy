from dp.planning import expand_config

# ruff: noqa: E402
# ruff: noqa: E402
# ruff: noqa: E402
"""Tests for Seeder dispatch policy."""
from dp.sync.seeder import dispatch_exists


def test_dispatch_exists_uses_run_id() -> None:
    assert dispatch_exists([(b"1-0", {b"__data__": b'"run_id":"r1"'})], "r1")
    assert not dispatch_exists([(b"1-0", {b"__data__": b'"run_id":"r1"'})], "r2")


"""Additional Seeder coverage."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from duckdb import connect
from redis.asyncio import Redis

from dp.models import SchemaConfig, SeedTask, SyncConfig, SyncPlan
from dp.sync.publisher import publish_schema, publisher
from dp.sync.seeder import cleanup_seeder_consumers, seed_sync, seeder


@pytest.mark.asyncio
async def test_seeder_groups_schemas_and_dispatches(
    sync_config_path: Path,
    redis: Redis,
    broker: object,
) -> None:
    sync_config_path.write_text(
        SyncConfig(
            schemas={"app": SchemaConfig(), "other": SchemaConfig()}
        ).model_dump_json()
    )
    fake = redis
    plans_key = "dp:plans:r1"
    await fake.hset(
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
async def test_seeder_cleanup_removes_consumers(redis: Redis) -> None:
    with (
        patch("dp.sync.seeder.cleanup_consumer", new_callable=AsyncMock) as cleanup,
    ):
        await cleanup_seeder_consumers()
    assert cleanup.await_count == 2


"""Pipeline branch coverage."""

from dp.models import (
    PartitionedTable,
)


def test_expand_config_skips_partitioned() -> None:
    db = connect(":memory:")
    assert expand_config([PartitionedTable(name="p.d.t")], "b", "r", db) == []


def test_seeder_dispatch_guard() -> None:
    assert dispatch_exists([], "r1") is False


@pytest.mark.asyncio
async def test_seeder_skips_existing_dispatch(
    sync_config_path: Path, redis: Redis
) -> None:
    with (
        patch("dp.sync.seeder.dispatch_exists", return_value=True),
        patch.object(seeder, "exit") as exit_app,
    ):
        await seed_sync(SeedTask(run_id="r1"))
    exit_app.assert_called_once()
