"""Unit tests for DBOS utility behavior."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from data_proxy.dbos import utils, workflows
from data_proxy.models import DumpResult, DumpStatus, SyncPlan
from tests.fixtures.types import Calls
from tests.helpers import dump_task, publish, run_dump_task


class TestTransientRetryClassification:
    """TransientRetryClassification behavior tests."""

    def test_classifies_runtime_errors_as_transient(self) -> None:
        """Classify runtime errors as transient."""
        assert utils.retry_transient(RuntimeError("temporary"))
        assert not utils.retry_transient(ValueError("invalid"))

    def test_classifies_cancellation_as_permanent(self) -> None:
        """Classify cancellation as permanent."""
        assert not utils.retry_transient(asyncio.CancelledError())


class TestPublishSchemaOrder:
    """publish_schema workflow behavior tests."""

    async def test_writes_state_only_after_the_reader_catches_up(
        self, calls: Calls
    ) -> None:
        await publish(SyncPlan(schema_name="app"))

        assert calls.names == [
            "commit_ducklake_snapshot",
            "wait_for_reader_snapshot",
            "record_publish_metrics",
            "commit_table_state",
        ]
        assert calls.waited == [("app", 42)]

    async def test_does_not_write_state_when_the_wait_times_out(
        self, calls: Calls
    ) -> None:
        calls.wait_error = TimeoutError("reader is behind")

        with pytest.raises(TimeoutError):
            await publish(SyncPlan(schema_name="app"))

        assert calls.names == ["commit_ducklake_snapshot", "wait_for_reader_snapshot"]

    async def test_skips_the_wait_when_nothing_was_published(
        self, calls: Calls
    ) -> None:
        calls.snapshot_id = None

        await publish(SyncPlan(schema_name="app"))

        assert calls.names == [
            "commit_ducklake_snapshot",
            "record_publish_metrics",
            "commit_table_state",
        ]


class TestDumpTask:
    """dump_task turns extraction errors into failed output paths."""

    async def test_returns_success_when_extraction_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record_failure = AsyncMock()
        monkeypatch.setattr(workflows, "extract_task", AsyncMock())
        monkeypatch.setattr(workflows, "record_dump_failure", record_failure)
        monkeypatch.setattr(workflows, "record_dump_metrics", AsyncMock())

        assert await run_dump_task(dump_task()) == DumpResult()
        record_failure.assert_not_awaited()

    async def test_marks_every_output_path_failed_when_extraction_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = dump_task()
        record_failure = AsyncMock()
        record_metrics = AsyncMock()
        monkeypatch.setattr(
            workflows, "extract_task", AsyncMock(side_effect=RuntimeError("boom"))
        )
        monkeypatch.setattr(workflows, "record_dump_failure", record_failure)
        monkeypatch.setattr(workflows, "record_dump_metrics", record_metrics)

        result = await run_dump_task(task)

        assert result == DumpResult(
            status=DumpStatus.FAILURE, failed_paths=task.output_paths
        )
        record_failure.assert_awaited_once_with(task, "boom")
        record_metrics.assert_awaited_once_with(
            task.task_id, task.table, task.target_schema, "error"
        )
