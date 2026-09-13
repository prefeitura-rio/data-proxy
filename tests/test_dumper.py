"""Tests for Dumper subscriptions."""

import logging
from typing import NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
from faststream.exceptions import StopApplication
from redis.asyncio import Redis

from dp.constants import SEED_STREAM
from dp.errors import retry_or_stop
from dp.models import AllSelection, DumpTask, SeedTask
from dp.sync.dumper import (
    cleanup_consumers,
    dump_task,
    dumper,
)

pytestmark = pytest.mark.usefixtures("test_settings", "metrics_disabled")

test_logger = logging.getLogger("test")


class TestDumpTask:
    """Tests for dump-task subscriber behavior."""

    @pytest.mark.asyncio
    async def test_dumper_exits_when_extraction_fails(
        self,
        redis: Redis,
        broker: object,
        standard_dump_task: DumpTask,
    ) -> None:
        """
        GIVEN: extraction raises RuntimeError.
        WHEN: dump_task runs.
        THEN: retry_or_stop re-publishes the task and raises StopApplication.
        """
        with (
            patch(
                "dp.sync.dumper.extract_task",
                new_callable=AsyncMock,
                side_effect=RuntimeError("bad"),
            ),
            patch("dp.sync.dumper.complete_dump", new_callable=AsyncMock),
            patch.object(dumper, "exit"),
            patch("dp.sync.dumper.broker.publish", new_callable=AsyncMock),
            pytest.raises(StopApplication),
        ):
            await dump_task(standard_dump_task, test_logger)


class TestDumperSeedDispatch:
    """Tests for seed dispatch after dump completion."""

    @pytest.mark.asyncio
    async def test_dumper_publishes_seed_publication_when_last_dump_completes(
        self,
        redis: Redis,
        broker: object,
        standard_dump_task: DumpTask,
    ) -> None:
        """
        GIVEN: the last dump completes with zero remaining.
        WHEN: dump_task runs.
        THEN: the dumper publishes a seed task for the run.
        """
        with (
            patch("dp.sync.dumper.extract_task", new_callable=AsyncMock),
            patch(
                "dp.sync.dumper.complete_dump",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("dp.sync.dumper.broker.publish", new_callable=AsyncMock) as publish,
        ):
            await dump_task(standard_dump_task, test_logger)

        message = publish.call_args.args[0]
        assert message == SeedTask(run_id="r1")
        assert publish.call_args.kwargs["stream"] == SEED_STREAM


class TestDumperCleanup:
    """Tests for dumper consumer cleanup."""

    @pytest.mark.asyncio
    async def test_dumper_cleanup_removes_idle_consumers(self, redis: Redis) -> None:
        """
        GIVEN: the dumper shutdown handler runs.
        WHEN: cleanup_consumers is called.
        THEN: it asks cleanup_consumer to remove each configured consumer.
        """
        with (
            patch("dp.utils.cleanup_consumer", new_callable=AsyncMock) as cleanup,
        ):
            await cleanup_consumers()
        assert cleanup.await_count == 2


class RetryCase(NamedTuple):
    """One retry_or_stop scenario and its observable outcome."""

    name: str
    retry_count: int
    max_retries: int
    republished: bool


RETRY_CASES = [
    RetryCase("below the limit", 0, 3, republished=True),
    RetryCase("at the limit", 3, 3, republished=False),
]


class TestRetryOrStop:
    """Tests for retry_or_stop error handling."""

    @pytest.mark.parametrize("case", RETRY_CASES, ids=lambda case: case.name)
    @pytest.mark.asyncio
    async def test_retry_or_stop_republishes_or_records_failure(
        self, case: RetryCase
    ) -> None:
        """
        GIVEN: a failed task below or at the retry limit.
        WHEN: retry_or_stop is called.
        THEN: below the limit it re-publishes, at the limit it records the failure.
        """
        task = DumpTask(
            run_id="r1",
            table="p.d.t",
            bucket_path="s3://b/t",
            selections=[AllSelection()],
            retry_count=case.retry_count,
        )
        publisher = AsyncMock()
        with (
            patch("dp.errors.DUMP_STREAM", "dp:extract"),
            patch("dp.errors.complete_dump", new_callable=AsyncMock) as complete,
            pytest.raises(StopApplication),
        ):
            await retry_or_stop(
                RuntimeError("bad"),
                task,
                publisher.publish,
                max_retries=case.max_retries,
            )

        if case.republished:
            publisher.publish.assert_called_once()
            republished = publisher.publish.call_args.args[0]
            assert republished.retry_count == case.retry_count + 1
            return

        publisher.publish.assert_not_called()
        complete.assert_awaited_once()
