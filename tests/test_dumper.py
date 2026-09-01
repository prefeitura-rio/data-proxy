"""Tests for Dumper subscriptions."""

from unittest.mock import AsyncMock, patch

import pytest
from duckdb import DuckDBPyConnection
from redis.asyncio import Redis

from dp.models import AllSelection, DumpTask
from dp.sync.dumper import (
    cleanup_dumper_consumers,
    dump_task,
    dumper,
    extract_task_wrapper,
    subs,
)
from dp.sync.seeder import seed_sync
from tests.helpers import dump as make_dump


def test_dumper_has_new_and_stale_subscriptions() -> None:
    assert subs["new"].min_idle_time is None
    assert subs["stale"].min_idle_time is not None


@pytest.mark.asyncio
async def test_dumper_records_failure_and_exits(redis: Redis) -> None:
    task = DumpTask(
        run_id="r1", table="p.d.t", bucket_path="s3://b", selection=AllSelection()
    )
    with (
        patch("dp.sync.dumper.extract_task_wrapper", side_effect=RuntimeError("bad")),
        patch("dp.sync.dumper.complete_dump", new_callable=AsyncMock, return_value=1),
        patch.object(dumper, "exit") as exit_app,
    ):
        await dump_task(task)
    exit_app.assert_called_once()


"""Additional Producer and Dumper coverage."""


def test_extract_wrapper(duckdb: DuckDBPyConnection) -> None:
    with (
        patch("dp.sync.dumper.connect", return_value=duckdb) as connect,
        patch("dp.sync.dumper.extract_task") as extract,
    ):
        extract_task_wrapper(make_dump())
    connect.assert_called_once()
    extract.assert_called_once()


@pytest.mark.asyncio
async def test_dumper_publishes_seed_for_last_dump(
    redis: Redis,
    broker: object,
) -> None:
    with (
        patch("dp.sync.dumper.extract_task_wrapper"),
        patch("dp.sync.dumper.complete_dump", new_callable=AsyncMock, return_value=0),
        patch.object(dumper, "exit"),
    ):
        await dump_task(make_dump())
    assert seed_sync.mock.call_count == 2
    seed_sync.mock.assert_called_with({"run_id": "r1"})


@pytest.mark.asyncio
async def test_dumper_cleanup_removes_consumers(redis: Redis) -> None:
    with (
        patch("dp.sync.dumper.cleanup_consumer", new_callable=AsyncMock) as cleanup,
    ):
        await cleanup_dumper_consumers()
    assert cleanup.await_count == 2
