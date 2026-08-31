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
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dp.models import SchemaConfig, SchemaWriters, SeedTask, SyncConfig, SyncPlan
from dp.sync.seeder import cleanup_seeder_consumers, seed_sync, seeder
from tests.helpers import FakeRedis


@pytest.mark.asyncio
async def test_seeder_groups_schemas_and_dispatches(
    config: Callable[[SyncConfig], None],
    valkey: FakeRedis,
    writers: SchemaWriters,
) -> None:
    config(SyncConfig(schemas={"app": SchemaConfig(), "other": SchemaConfig()}))
    fake = valkey
    plans_key = "dp:plans:r1"
    fake.hashes[plans_key] = {
        "app": SyncPlan(schema_name="app").model_dump_json(),
        "other": SyncPlan(schema_name="other").model_dump_json(),
    }
    publish = AsyncMock()
    publisher = MagicMock(publish=publish)
    with (
        patch("dp.settings.Settings.schema_writers", return_value=writers),
        patch("dp.sync.seeder.broker.publisher", return_value=publisher),
        patch("dp.sync.seeder.psycopg.connect", return_value=MagicMock()),
        patch("dp.sync.seeder.initialize_schemas"),
        patch.object(seeder, "exit"),
    ):
        await seed_sync(SeedTask(run_id="r1"))
    assert publish.await_count == 2


@pytest.mark.asyncio
async def test_seeder_cleanup_removes_consumers(valkey: FakeRedis) -> None:
    with (
        patch("dp.sync.seeder.cleanup_consumer", new_callable=AsyncMock) as cleanup,
    ):
        await cleanup_seeder_consumers()
    assert cleanup.await_count == 2


"""Pipeline branch coverage."""

from dp.models import (
    PartitionedTable,
)
from tests.helpers import FakeDuckDBConnection


def test_expand_config_skips_partitioned() -> None:
    db = FakeDuckDBConnection()
    assert expand_config([PartitionedTable(name="p.d.t")], "b", "r", db) == []


def test_seeder_dispatch_guard() -> None:
    assert dispatch_exists([], "r1") is False


@pytest.mark.asyncio
async def test_seeder_skips_existing_dispatch(path: Path, valkey: FakeRedis) -> None:
    with (
        patch("dp.sync.seeder.dispatch_exists", return_value=True),
        patch.object(seeder, "exit") as exit_app,
    ):
        await seed_sync(SeedTask(run_id="r1"))
    exit_app.assert_called_once()
