"""Tests for the FastStream producer orchestrator."""

from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import ANY, AsyncMock, call, patch

import pytest
from faststream import TestApp
from helpers import FakeRedis, redis_client

from dp.constants import (
    FINALIZERS_GROUP,
    SYNC_FINALIZE_STREAM,
    SYNC_TASKS_STREAM,
    WORKERS_GROUP,
)
from dp.models import AllSelection, FinalizeMessage, SyncPlan, SyncTask
from dp.state import create_run
from dp.sync.producer import producer, recover_lost_finalization


def state_patches() -> tuple[AbstractContextManager[object], ...]:
    """Return common state-operation patches for producer tests."""
    return (
        patch("dp.sync.producer.trim_stale_entries", new_callable=AsyncMock),
        patch("dp.sync.producer.create_consumer_group", new_callable=AsyncMock),
        patch(
            "dp.sync.producer.recover_lost_finalization",
            new_callable=AsyncMock,
            return_value=None,
        ),
    )


@pytest.mark.asyncio
async def test_recovery_without_active_run_returns_none() -> None:
    """A producer with no active run has nothing to recover."""
    assert await recover_lost_finalization(redis_client(FakeRedis())) is None


@pytest.mark.asyncio
async def test_recovery_keeps_extracting_run_untouched() -> None:
    """A run with pending tasks is not finalized early."""
    fake = FakeRedis()
    await create_run(redis_client(fake), SyncPlan(sync_id="s1"), 2)

    assert await recover_lost_finalization(redis_client(fake)) is None


@pytest.mark.asyncio
async def test_recovery_skips_when_finalizer_message_in_flight() -> None:
    """A run with a pending finalizer message is not re-published."""
    fake = FakeRedis(pending_groups={("dp:sync:finalize", "finalizers")})
    await create_run(redis_client(fake), SyncPlan(sync_id="s1"), 0)

    with patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish:
        assert await recover_lost_finalization(redis_client(fake)) is None

    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_republishes_lost_finalizer_message() -> None:
    """A fully extracted run with no finalizer message is finalized again."""
    fake = FakeRedis()
    await create_run(redis_client(fake), SyncPlan(sync_id="s1"), 0)

    with patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish:
        assert await recover_lost_finalization(redis_client(fake)) == "s1"

    publish.assert_awaited_once_with(
        FinalizeMessage(sync_id="s1"),
        stream="dp:sync:finalize",
    )


@pytest.mark.asyncio
async def test_exits_when_recovery_republishes_finalizer(
    sync_config_path: Path,
) -> None:
    """A recovered run exits without building a new plan."""
    with (
        patch("dp.sync.producer.trim_stale_entries", new_callable=AsyncMock),
        patch("dp.sync.producer.create_consumer_group", new_callable=AsyncMock),
        patch(
            "dp.sync.producer.recover_lost_finalization",
            new_callable=AsyncMock,
            return_value="s1",
        ),
        patch("dp.sync.producer.build_sync_plan") as build,
        patch("dp.sync.producer.create_run", new_callable=AsyncMock) as create_run,
        patch(
            "dp.sync.producer.broker.publish",
            new_callable=AsyncMock,
        ) as publish,
        patch.object(producer, "exit") as exit_app,
    ):
        async with TestApp(producer):
            pass

    build.assert_not_awaited()
    create_run.assert_not_awaited()
    publish.assert_not_awaited()
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_exits_when_planning_finds_no_tasks(
    sync_config_path: Path,
) -> None:
    """An unchanged run exits without state or message publication."""
    trim, group, recover = state_patches()
    with (
        trim,
        group,
        recover,
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
    trim, group, recover = state_patches()
    with (
        trim,
        group,
        recover,
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
    trim, _, recover = state_patches()
    with (
        trim,
        recover,
        patch(
            "dp.sync.producer.create_consumer_group",
            new_callable=AsyncMock,
        ) as create_group,
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
    create_group.assert_has_awaits(
        [
            call(ANY, SYNC_TASKS_STREAM, WORKERS_GROUP),
            call(ANY, SYNC_FINALIZE_STREAM, FINALIZERS_GROUP),
        ]
    )
    publish.assert_awaited_once_with(task, stream="dp:sync:tasks")
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_deletion_only_plan_publishes_finalizer_directly(
    sync_config_path: Path,
) -> None:
    """A plan without extraction tasks bypasses the worker counter."""
    plan = SyncPlan(sync_id="s1")
    trim, group, recover = state_patches()
    with (
        trim,
        group,
        recover,
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
