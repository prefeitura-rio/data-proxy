"""Tests for the FastStream producer orchestrator."""

from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

import pytest
from faststream import TestApp

from dp.models import AllSelection, FinalizeMessage, SyncPlan, SyncTask
from dp.sync.producer import producer


def state_patches() -> tuple[AbstractContextManager[object], ...]:
    """Return common state-operation patches for producer tests."""
    return (
        patch("dp.sync.producer.trim_stale_entries", new_callable=AsyncMock),
        patch("dp.sync.producer.create_consumer_group", new_callable=AsyncMock),
    )


@pytest.mark.asyncio
async def test_exits_when_planning_finds_no_tasks(
    sync_config_path: Path,
) -> None:
    """An unchanged run exits without state or message publication."""
    trim, group = state_patches()
    with (
        trim,
        group,
        patch(
            "dp.sync.producer.build_sync_plan",
            new_callable=AsyncMock,
            return_value=(None, []),
        ),
        patch("dp.sync.producer.create_run", new_callable=AsyncMock) as create_run,
        patch(
            "dp.sync.producer.broker.publish",
            new_callable=AsyncMock,
        ) as publish,
        patch.object(producer, "exit") as exit_app,
    ):
        async with TestApp(producer):
            pass

    create_run.assert_not_awaited()
    publish.assert_not_awaited()
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_exits_when_atomic_run_creation_conflicts(
    sync_config_path: Path,
) -> None:
    """An active run prevents task publication."""
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.d.t": "100"},
        paths={"p.d.t": ["s3://bucket/t/data.parquet"]},
    )
    task = SyncTask(
        sync_id="s1",
        table="p.d.t",
        bucket_path="s3://bucket/t/data.parquet",
        selection=AllSelection(),
    )
    trim, group = state_patches()
    with (
        trim,
        group,
        patch(
            "dp.sync.producer.build_sync_plan",
            new_callable=AsyncMock,
            return_value=(plan, [task]),
        ),
        patch(
            "dp.sync.producer.create_run",
            new_callable=AsyncMock,
            return_value=False,
        ) as create_run,
        patch(
            "dp.sync.producer.broker.publish",
            new_callable=AsyncMock,
        ) as publish,
        patch.object(producer, "exit") as exit_app,
    ):
        async with TestApp(producer):
            pass

    create_run.assert_awaited_once_with(ANY, plan, 1)
    publish.assert_not_awaited()
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_creates_run_before_publishing_tasks(
    sync_config_path: Path,
) -> None:
    """A changed run creates state before it publishes every task."""
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.d.t": "100"},
        paths={"p.d.t": ["s3://bucket/t/data.parquet"]},
    )
    task = SyncTask(
        sync_id="s1",
        table="p.d.t",
        bucket_path="s3://bucket/t/data.parquet",
        selection=AllSelection(),
    )
    trim, group = state_patches()
    with (
        trim,
        group,
        patch(
            "dp.sync.producer.build_sync_plan",
            new_callable=AsyncMock,
            return_value=(plan, [task]),
        ),
        patch(
            "dp.sync.producer.create_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as create_run,
        patch(
            "dp.sync.producer.broker.publish",
            new_callable=AsyncMock,
        ) as publish,
        patch.object(producer, "exit") as exit_app,
    ):
        async with TestApp(producer):
            pass

    create_run.assert_awaited_once_with(ANY, plan, 1)
    publish.assert_awaited_once_with(task, stream="dp:sync:tasks")
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_deletion_only_plan_publishes_finalizer_directly(
    sync_config_path: Path,
) -> None:
    """A plan without extraction tasks bypasses the worker counter."""
    plan = SyncPlan(sync_id="s1")
    trim, group = state_patches()
    with (
        trim,
        group,
        patch(
            "dp.sync.producer.build_sync_plan",
            new_callable=AsyncMock,
            return_value=(plan, []),
        ),
        patch(
            "dp.sync.producer.create_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as create_run,
        patch(
            "dp.sync.producer.broker.publish",
            new_callable=AsyncMock,
        ) as publish,
        patch.object(producer, "exit"),
    ):
        async with TestApp(producer):
            pass

    create_run.assert_awaited_once_with(ANY, plan, 0)
    publish.assert_awaited_once_with(
        FinalizeMessage(sync_id=plan.sync_id), stream="dp:sync:finalize"
    )
