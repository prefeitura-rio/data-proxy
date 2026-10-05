"""Unit tests for DBOS utility behavior."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from data_proxy.dbos import steps, utils, workflows
from data_proxy.models import DumpResult, DumpStatus, SyncPlan
from data_proxy.settings import settings
from tests.fixtures.types import Calls
from tests.helpers import (
    catalog_commit_error,
    dump_task,
    publish,
    run_dump_task,
    run_sync,
    stub_sync_run,
    workflow_body,
)


class TestTransientRetryClassification:
    """TransientRetryClassification behavior tests."""

    def test_classifies_runtime_errors_as_transient(self) -> None:
        """Classify runtime errors as transient."""
        assert utils.retry_transient(RuntimeError("temporary"))
        assert not utils.retry_transient(ValueError("invalid"))

    def test_classifies_cancellation_as_permanent(self) -> None:
        """Classify cancellation as permanent."""
        assert not utils.retry_transient(asyncio.CancelledError())


class TestCatalogLockRetryClassification:
    """retry_catalog_locked retries only the DuckLake catalog lock error."""

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            pytest.param(
                catalog_commit_error("database is locked"),
                True,
                id="the lock error of a DuckLake commit",
            ),
            pytest.param(
                catalog_commit_error("UNIQUE constraint failed"),
                False,
                id="another DuckLake commit error",
            ),
            pytest.param(RuntimeError("temporary"), False, id="a runtime error"),
            pytest.param(
                RuntimeError("database is locked"),
                False,
                id="the same message from another error type",
            ),
            pytest.param(asyncio.CancelledError(), False, id="a cancellation"),
        ],
    )
    def test_retries_only_the_catalog_lock_error(
        self, error: BaseException, expected: bool
    ) -> None:
        assert utils.retry_catalog_locked(error) is expected


class TestRunSyncRestartsDeployments:
    """run_sync refreshes schema clients and DuckDB catalog backends separately."""

    @pytest.mark.parametrize(
        (
            "postgrest_restart_required",
            "published",
            "pooler_restarts",
            "postgrest_restarts",
        ),
        [
            pytest.param(
                False,
                set[str](),
                0,
                0,
                id="nothing-changed-and-nothing-published",
            ),
            pytest.param(
                False,
                {"t"},
                1,
                0,
                id="tables-published-and-no-postgrest-restart",
            ),
            pytest.param(
                True,
                set[str](),
                0,
                1,
                id="postgrest-restart-and-nothing-published",
            ),
            pytest.param(
                True,
                {"t"},
                1,
                1,
                id="postgrest-restart-and-tables-published",
            ),
        ],
    )
    async def test_restarts_only_the_required_deployments(
        self,
        monkeypatch: pytest.MonkeyPatch,
        postgrest_restart_required: bool,
        published: set[str],
        pooler_restarts: int,
        postgrest_restarts: int,
    ) -> None:
        restart_pooler, restart_postgrest, events = stub_sync_run(
            monkeypatch,
            postgrest_restart_required=postgrest_restart_required,
            published=published,
        )

        await run_sync()

        assert restart_pooler.await_count == pooler_restarts
        assert restart_postgrest.await_count == postgrest_restarts
        assert events == ["enqueue:app", "wait:app"]

    @pytest.mark.asyncio
    async def test_restarts_postgrest_once_for_multiple_schema_plans(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Restart global PostgREST Deployments once after one sync."""
        restart_pooler, restart_postgrest, events = stub_sync_run(
            monkeypatch,
            postgrest_restart_required=True,
            published=set(),
            plans=[SyncPlan(schema_name="one"), SyncPlan(schema_name="two")],
        )

        await run_sync()

        restart_pooler.assert_not_awaited()
        restart_postgrest.assert_awaited_once_with("run")
        assert events == [
            "enqueue:one",
            "enqueue:two",
            "wait:one",
            "wait:two",
        ]

    @pytest.mark.asyncio
    async def test_enqueues_all_dumps_before_waiting_for_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Queue every dump child workflow before durable fan-in starts."""
        first = dump_task(table="p.d.first")
        second = dump_task(table="p.d.second")
        restart_pooler, restart_postgrest, events = stub_sync_run(
            monkeypatch,
            postgrest_restart_required=False,
            published=set(),
            tasks=[first, second],
        )

        await run_sync()

        restart_pooler.assert_not_awaited()
        restart_postgrest.assert_not_awaited()
        assert events == [
            "enqueue:p.d.first",
            "enqueue:p.d.second",
            "wait:p.d.first",
            "wait:p.d.second",
            "enqueue:app",
            "wait:app",
        ]


class TestRestartPostgrestStep:
    """The restart step covers the Deployments that the chart lists."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "names",
        [
            pytest.param(["data-proxy-postgrest"], id="single-mode"),
            pytest.param(
                ["data-proxy-postgrest", "data-proxy-postgrest-ro"], id="ha-mode"
            ),
        ],
    )
    async def test_restarts_the_configured_deployments(
        self, monkeypatch: pytest.MonkeyPatch, names: list[str]
    ) -> None:
        """Pass every configured Deployment name to the restart."""
        restart = AsyncMock()
        monkeypatch.setattr(steps, "restart_deployments", restart)
        monkeypatch.setattr(settings, "POSTGREST_DEPLOYMENTS", names)

        await workflow_body(steps.restart_postgrest)("run")

        assert restart.await_args is not None
        assert restart.await_args.kwargs["names"] == names
        assert restart.await_args.kwargs["kind"] == "PostgREST"


class TestRestartPoolerStep:
    """The restart step covers the Pooler Deployments that the chart lists."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "names",
        [
            pytest.param(["data-proxy-pooler"], id="single-mode"),
            pytest.param(["data-proxy-pooler", "data-proxy-pooler-ro"], id="ha-mode"),
        ],
    )
    async def test_restarts_the_configured_deployments(
        self, monkeypatch: pytest.MonkeyPatch, names: list[str]
    ) -> None:
        """Pass every configured Pooler Deployment name to the restart."""
        restart = AsyncMock()
        monkeypatch.setattr(steps, "restart_deployments", restart)
        monkeypatch.setattr(settings, "POOLER_DEPLOYMENTS", names)

        await workflow_body(steps.restart_pooler)("run")

        assert restart.await_args is not None
        assert restart.await_args.kwargs["names"] == names
        assert restart.await_args.kwargs["kind"] == "PgBouncer"


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
