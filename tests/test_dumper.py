# ruff: noqa: E402
# ruff: noqa: E402
"""Tests for Dumper subscriptions."""

from dp.sync.dumper import subs


def test_dumper_has_new_and_stale_subscriptions() -> None:
    assert subs["new"].min_idle_time is None
    assert subs["stale"].min_idle_time is not None


"""Coverage for Dumper branches."""
from unittest.mock import AsyncMock, patch

import pytest

from dp.models import AllSelection, DumpTask
from dp.sync.dumper import cleanup_dumper_consumers, dump_task, dumper
from tests.helpers import FakeRedis


@pytest.mark.asyncio
async def test_dumper_records_failure_and_exits(valkey: FakeRedis) -> None:
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


from dp.models import SeedTask
from dp.sync.dumper import extract_task_wrapper
from tests.helpers import FakeDuckDBConnection
from tests.helpers import dump as make_dump


def test_extract_wrapper(duckdb: FakeDuckDBConnection) -> None:
    with (
        patch("dp.sync.dumper.connect", return_value=duckdb) as connect,
        patch("dp.sync.dumper.extract_task") as extract,
    ):
        extract_task_wrapper(make_dump())
    connect.assert_called_once()
    extract.assert_called_once()


@pytest.mark.asyncio
async def test_dumper_publishes_seed_for_last_dump(valkey: FakeRedis) -> None:
    with (
        patch("dp.sync.dumper.extract_task_wrapper"),
        patch("dp.sync.dumper.complete_dump", new_callable=AsyncMock, return_value=0),
        patch("dp.sync.dumper.broker.publish", new_callable=AsyncMock) as publish,
        patch.object(dumper, "exit"),
    ):
        await dump_task(make_dump())
    publish.assert_awaited_once_with(SeedTask(run_id="r1"), stream="dp:prepare")


@pytest.mark.asyncio
async def test_dumper_cleanup_removes_consumers(valkey: FakeRedis) -> None:
    with (
        patch("dp.sync.dumper.cleanup_consumer", new_callable=AsyncMock) as cleanup,
    ):
        await cleanup_dumper_consumers()
    assert cleanup.await_count == 2
