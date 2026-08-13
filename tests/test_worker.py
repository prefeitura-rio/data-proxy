"""Tests for the FastStream worker orchestrator."""

from unittest.mock import AsyncMock, patch

import pytest
from faststream.exceptions import StopApplication
from faststream.redis.testing import TestRedisBroker

from dp.constants import SYNC_SHUTDOWN_CHANNEL, SYNC_TASKS_STREAM
from dp.models import ShutdownMessage, SyncTask
from dp.sync.worker import broker, process_shard, worker

TASK = SyncTask(
    sync_id="s1",
    bq_table="p.d.t",
    gcs_path="s3://bucket/t/data.parquet",
)


@pytest.mark.asyncio
async def test_extracts_and_completes_task() -> None:
    """A subscriber delegates extraction and state completion."""
    with (
        patch("dp.sync.worker.extract_task") as extract,
        patch(
            "dp.sync.worker.complete_task",
            new_callable=AsyncMock,
            return_value=(1, 3),
        ) as complete,
    ):
        await process_shard(TASK)

    extract.assert_called_once_with(TASK)
    complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_publishes_finalizer_when_counter_reaches_zero() -> None:
    """The last task publishes one finalizer message."""
    with (
        patch("dp.sync.worker.extract_task"),
        patch(
            "dp.sync.worker.complete_task",
            new_callable=AsyncMock,
            return_value=(0, 1),
        ),
        patch("dp.sync.worker.broker.publish", new_callable=AsyncMock) as publish,
    ):
        await process_shard(TASK)

    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_exits_when_stream_lag_is_zero() -> None:
    """A worker exits when no undelivered stream messages remain."""
    with (
        patch("dp.sync.worker.extract_task"),
        patch(
            "dp.sync.worker.complete_task",
            new_callable=AsyncMock,
            return_value=(1, 0),
        ),
        patch.object(worker, "exit") as exit_app,
    ):
        await process_shard(TASK)

    exit_app.assert_called_once()


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
