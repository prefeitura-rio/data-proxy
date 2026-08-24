"""Tests for the FastStream finalizer orchestrator."""

from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

import pytest
from faststream.exceptions import StopApplication
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection, FakePgConn, FakeRedis, FakeRedisCM

from dp.models import FinalizeMessage, PublicationResult, SyncPlan
from dp.sync.finalizer import broker, ensure_consumer_group, finalize_sync, finalizer


def patch_make_redis() -> AbstractContextManager[object]:
    """Patch Settings.make_redis to return a fresh fake Redis context."""
    return patch(
        "dp.settings.Settings.make_redis",
        return_value=FakeRedisCM(FakeRedis()),
    )


@pytest.mark.asyncio
async def test_startup_ensures_consumer_group() -> None:
    """Finalizer startup delegates consumer-group creation to state operations."""
    with (
        patch_make_redis(),
        patch(
            "dp.sync.finalizer.create_consumer_group",
            new_callable=AsyncMock,
        ) as create,
    ):
        await ensure_consumer_group()

    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_applies_plan_commits_state_and_exits(
    sync_config_path: Path,
) -> None:
    """Successful orchestration applies one plan before committing state."""
    plan = SyncPlan(sync_id="s1", signatures={}, paths={})

    with (
        patch_make_redis(),
        patch(
            "dp.sync.finalizer.read_sync_plan",
            new_callable=AsyncMock,
            return_value=plan,
        ) as read,
        patch("dp.sync.finalizer.psycopg.connect", return_value=FakePgConn()),
        patch(
            "dp.sync.finalizer.connect",
            return_value=FakeDuckDBConnection(),
        ),
        patch(
            "dp.sync.finalizer.apply_sync_plan",
            return_value=PublicationResult(plan=plan, published_tables={"p.d.t"}),
        ) as apply,
        patch(
            "dp.sync.finalizer.commit_sync_state",
            new_callable=AsyncMock,
        ) as commit,
        patch(
            "dp.sync.finalizer.broker.publish",
            new_callable=AsyncMock,
        ) as publish,
        patch.object(finalizer, "exit") as exit_app,
    ):
        await finalize_sync(FinalizeMessage(sync_id="s1"))

    publish.assert_awaited_once()
    read.assert_awaited_once()
    apply.assert_called_once()
    commit.assert_awaited_once_with(ANY, plan, {"p.d.t"})
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_loading_failure_stops_application(
    sync_config_path: Path,
) -> None:
    """Domain failures terminate the finite Job with a non-zero status."""
    plan = SyncPlan(sync_id="s1", signatures={}, paths={})

    async with TestRedisBroker(broker) as test_broker:
        with (
            patch_make_redis(),
            patch(
                "dp.sync.finalizer.read_sync_plan",
                new_callable=AsyncMock,
                return_value=plan,
            ),
            patch("dp.sync.finalizer.psycopg.connect", return_value=FakePgConn()),
            patch(
                "dp.sync.finalizer.connect",
                return_value=FakeDuckDBConnection(),
            ),
            patch(
                "dp.sync.finalizer.apply_sync_plan",
                side_effect=RuntimeError("failed"),
            ),
            pytest.raises(StopApplication) as result,
        ):
            await test_broker.publish(
                FinalizeMessage(sync_id="s1"),
                stream="dp:sync:finalize",
            )

    assert result.value.code == 1
