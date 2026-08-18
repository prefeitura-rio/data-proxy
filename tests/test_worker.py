"""Tests for the FastStream worker orchestrator."""

from unittest.mock import AsyncMock, patch

import pytest
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection

from dp.constants import SYNC_SHUTDOWN_CHANNEL
from dp.models import AllSelection, ShutdownMessage, SyncTask
from dp.sync.worker import broker, process_shard, worker

TASK = SyncTask(
    sync_id="s1",
    table="p.d.t",
    bucket_path="s3://bucket/t/data.parquet",
    selection=AllSelection(),
)


@pytest.mark.parametrize(
    ("remaining", "expect_publish"),
    [
        (1, False),
        (0, True),
    ],
)
@pytest.mark.asyncio
async def test_process_shard_branches_on_counter(
    remaining: int, expect_publish: bool
) -> None:
    """Task completion delegates extraction and publishes once the counter hits zero."""
    db = FakeDuckDBConnection()
    with (
        patch("dp.sync.worker.connect", return_value=db),
        patch("dp.sync.worker.extract_task") as extract,
        patch(
            "dp.sync.worker.complete_task",
            new_callable=AsyncMock,
            return_value=remaining,
        ) as complete,
        patch("dp.sync.worker.broker.publish", new_callable=AsyncMock) as publish,
    ):
        await process_shard(TASK)

    extract.assert_called_once_with(TASK, db)
    complete.assert_awaited_once()
    assert publish.await_count == (1 if expect_publish else 0)


@pytest.mark.asyncio
async def test_extraction_failure_is_logged_and_skipped() -> None:
    """An extraction error is logged, then the task still completes normally.

    A single table or partition failing to extract must not stop the
    worker: the error is logged explicitly and the run proceeds to
    account for the task and check whether to finalize, exactly as if
    extraction had succeeded.
    """
    with (
        patch("dp.sync.worker.connect", return_value=FakeDuckDBConnection()),
        patch("dp.sync.worker.extract_task", side_effect=RuntimeError("failed")),
        patch(
            "dp.sync.worker.complete_task",
            new_callable=AsyncMock,
            return_value=1,
        ) as complete,
        patch("dp.sync.worker.broker.publish", new_callable=AsyncMock) as publish,
    ):
        await process_shard(TASK)

    complete.assert_awaited_once()
    publish.assert_not_called()


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
