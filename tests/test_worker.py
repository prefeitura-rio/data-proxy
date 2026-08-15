"""Tests for the FastStream worker orchestrator."""

from unittest.mock import AsyncMock, patch

import pytest
from faststream.exceptions import StopApplication
from faststream.redis.testing import TestRedisBroker

from dp.constants import SYNC_SHUTDOWN_CHANNEL, SYNC_TASKS_STREAM
from dp.models import AllSelection, ShutdownMessage, SyncTask
from dp.sync.worker import broker, process_shard, worker

TASK = SyncTask(
    sync_id="s1",
    bq_table="p.d.t",
    gcs_path="s3://bucket/t/data.parquet",
    selection=AllSelection(),
)


@pytest.mark.parametrize(
    ("remaining", "lag", "expect_publish", "expect_exit"),
    [
        (1, 3, False, False),
        (0, 1, True, False),
        (1, 0, False, True),
    ],
)
@pytest.mark.asyncio
async def test_process_shard_branches_on_counter_and_lag(
    remaining: int, lag: int, expect_publish: bool, expect_exit: bool
) -> None:
    """Task completion delegates extraction and branches on counter and lag."""
    with (
        patch("dp.sync.worker.extract_task") as extract,
        patch(
            "dp.sync.worker.complete_task",
            new_callable=AsyncMock,
            return_value=(remaining, lag),
        ) as complete,
        patch("dp.sync.worker.broker.publish", new_callable=AsyncMock) as publish,
        patch.object(worker, "exit") as exit_app,
    ):
        await process_shard(TASK)

    extract.assert_called_once_with(TASK)
    complete.assert_awaited_once()
    assert publish.await_count == (1 if expect_publish else 0)
    assert exit_app.call_count == (1 if expect_exit else 0)


@pytest.mark.asyncio
async def test_failure_stops_application() -> None:
    """Extraction errors terminate the finite Job with a failure status."""
    async with TestRedisBroker(broker) as test_broker:
        with (
            patch("dp.sync.worker.extract_task", side_effect=RuntimeError("failed")),
            pytest.raises(StopApplication) as result,
        ):
            await test_broker.publish(TASK, stream=SYNC_TASKS_STREAM)

    assert result.value.code == 1


@pytest.mark.asyncio
async def test_shutdown_message_exits_worker() -> None:
    """The finalizer shutdown broadcast exits each worker."""
    async with TestRedisBroker(broker) as test_broker:
        with patch.object(worker, "exit") as exit_app:
            await test_broker.publish(
                ShutdownMessage(sync_id="s1"),
                SYNC_SHUTDOWN_CHANNEL,
            )

    exit_app.assert_called_once()
