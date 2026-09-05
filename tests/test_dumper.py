"""Tests for Dumper subscriptions."""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from duckdb import DuckDBPyConnection
from faststream.exceptions import StopApplication
from redis.asyncio import Redis

from dp.errors import retry_or_stop
from dp.models import AllSelection, DumpTask
from dp.sync.dumper import (
    cleanup_consumers,
    dump_task,
    dumper,
    extract_task_wrapper,
)
from dp.sync.seeder import seed_sync

pytestmark = pytest.mark.usefixtures("test_settings", "mock_push_to_gateway")

test_logger = logging.getLogger("test")


class TestDumper:
    """Tests for dump subscriber behavior."""

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
                "dp.sync.dumper.extract_task_wrapper", side_effect=RuntimeError("bad")
            ),
            patch("dp.sync.dumper.complete_dump", new_callable=AsyncMock),
            patch.object(dumper, "exit"),
            patch("dp.sync.dumper.broker.publish", new_callable=AsyncMock),
            pytest.raises(StopApplication),
        ):
            await dump_task(standard_dump_task, test_logger)

    def test_extract_wrapper_uses_duckdb_fixture(
        self, duckdb: DuckDBPyConnection, standard_dump_task: DumpTask
    ) -> None:
        """
        GIVEN: a dump task and a patched DuckDB connection.
        WHEN: extract_task_wrapper is called.
        THEN: it connects and extracts exactly once.
        """
        with (
            patch("dp.sync.dumper.connect", return_value=duckdb) as connect,
            patch("dp.sync.dumper.extract_task") as extract,
        ):
            extract_task_wrapper(standard_dump_task)
        connect.assert_called_once()
        extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_dumper_publishes_seed_sync_when_last_dump_completes(
        self,
        redis: Redis,
        broker: object,
        standard_dump_task: DumpTask,
    ) -> None:
        """
        GIVEN: the last dump completes with zero remaining.
        WHEN: dump_task runs.
        THEN: the dumper publishes a seed sync message.
        """
        with (
            patch("dp.sync.dumper.extract_task_wrapper"),
            patch(
                "dp.sync.dumper.complete_dump", new_callable=AsyncMock, return_value=0
            ),
            patch.object(dumper, "exit"),
        ):
            await dump_task(standard_dump_task, test_logger)
        assert seed_sync.mock.call_count == 2
        seed_sync.mock.assert_called_with({"run_id": "r1"})

    @pytest.mark.asyncio
    async def test_dumper_cleanup_removes_idle_consumers(self, redis: Redis) -> None:
        """
        GIVEN: the dumper shutdown handler runs.
        WHEN: cleanup_consumers is called.
        THEN: it asks cleanup_consumer to remove each configured consumer.
        """
        with (
            patch("dp.sync.dumper.cleanup_consumer", new_callable=AsyncMock) as cleanup,
        ):
            await cleanup_consumers()
        assert cleanup.await_count == 2


class TestRetryOrStop:
    """Tests for retry_or_stop error handling."""

    @pytest.mark.asyncio
    async def test_retry_republishes_with_incremented_count(self) -> None:
        """
        GIVEN: a failed task with retry_count=0 and max_retries=3.
        WHEN: retry_or_stop is called.
        THEN: the task is re-published with retry_count=1 and StopApplication is raised.
        """
        task = DumpTask(
            run_id="r1",
            table="p.d.t",
            bucket_path="s3://b/t",
            selection=AllSelection(),
        )
        broker = AsyncMock()
        with (
            patch("dp.errors.DUMP_STREAM", "dp:extract"),
            pytest.raises(StopApplication),
        ):
            await retry_or_stop(
                RuntimeError("bad"), task, broker.publish, max_retries=3
            )
        broker.publish.assert_called_once()
        republished = broker.publish.call_args.args[0]
        assert republished.retry_count == 1

    @pytest.mark.asyncio
    async def test_retry_at_limit_records_failure_and_stops(self) -> None:
        """
        GIVEN: a failed task with retry_count=3 and max_retries=3.
        WHEN: retry_or_stop is called.
        THEN: the task is not re-published and StopApplication is raised.
        """
        task = DumpTask(
            run_id="r1",
            table="p.d.t",
            bucket_path="s3://b/t",
            selection=AllSelection(),
            retry_count=3,
        )
        broker = AsyncMock()
        with (
            patch("dp.errors.complete_dump", new_callable=AsyncMock) as complete,
            pytest.raises(StopApplication),
        ):
            await retry_or_stop(
                RuntimeError("bad"), task, broker.publish, max_retries=3
            )
        broker.publish.assert_not_called()
        complete.assert_awaited_once()
