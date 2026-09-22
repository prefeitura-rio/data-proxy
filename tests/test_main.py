"""Tests for DBOS application startup and orchestration helpers."""

import threading
from unittest.mock import AsyncMock, patch

import pytest

import dp.main as application
from dp.constants import DUMP_QUEUE, PUBLISH_QUEUE, SYNC_QUEUE
from dp.models import (
    AllSelection,
    DumpFailure,
    DumpTask,
    SchemaConfig,
    SchemaWriters,
    SyncConfig,
    SyncPlan,
)


def test_main_registers_dbos_queues_and_schedule() -> None:
    with (
        patch.object(application, "DBOS") as dbos,
        patch.object(threading, "Event") as event,
    ):
        application.main()

    dbos.assert_called_once()
    dbos.listen_queues.assert_called_once_with([SYNC_QUEUE, DUMP_QUEUE, PUBLISH_QUEUE])
    dbos.launch.assert_called_once_with()
    assert dbos.register_queue.call_count == 3
    dbos.apply_schedules.assert_called_once()
    event.return_value.wait.assert_called_once_with()


def test_group_schema_configs_by_dsn() -> None:
    config = SyncConfig(schemas={"alpha": SchemaConfig(), "beta": SchemaConfig()})
    plans = [SyncPlan(schema_name="alpha"), SyncPlan(schema_name="beta")]
    writers = SchemaWriters(writers={"alpha": "dsn", "beta": "dsn"})

    assert application.group_schema_configs_by_dsn(plans, config, writers) == {
        "dsn": {"alpha": config.schemas["alpha"], "beta": config.schemas["beta"]}
    }


@pytest.mark.asyncio
async def test_run_dump_tasks_returns_failed_paths() -> None:
    task = DumpTask(
        run_id="run",
        table="project.schema.table",
        target_schema="schema",
        bucket_path="s3://bucket/table/data.parquet",
        selections=[AllSelection()],
    )
    handle = AsyncMock()
    handle.get_result.return_value = DumpFailure(failed_path=task.bucket_path)

    with patch("dp.main.DBOS.enqueue_workflow_async", return_value=handle) as enqueue:
        failed_paths = await application.run_dump_tasks([task])

    assert failed_paths == {task.bucket_path}
    enqueue.assert_called_once_with(DUMP_QUEUE, application.dump_task, task)


@pytest.mark.asyncio
async def test_run_publish_tasks_waits_for_every_schema() -> None:
    plan = SyncPlan(
        schema_name="schema",
        signatures={"project.schema.table": "signature"},
        paths={"project.schema.table": ["s3://bucket/table/data.parquet"]},
    )
    handle = AsyncMock()
    handle.get_result.return_value = {"project.schema.table"}

    with patch("dp.main.DBOS.enqueue_workflow_async", return_value=handle) as enqueue:
        await application.run_publish_tasks("run", [plan], {"failed.parquet"})

    enqueue.assert_called_once_with(
        PUBLISH_QUEUE,
        application.publish_schema,
        "run",
        plan,
        {"failed.parquet"},
    )
