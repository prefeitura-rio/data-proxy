"""Tests for the FastStream producer orchestrator."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from faststream import TestApp

from dp.models import SyncPlan, SyncTask
from dp.settings import settings
from dp.sync.producer import producer


@pytest.mark.asyncio
async def test_exits_when_planning_finds_no_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged run exits without state or message publication."""
    config = tmp_path / "sync.json"
    config.write_text('{"tables": []}')
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", config)

    with (
        patch(
            "dp.sync.producer.plan_sync",
            new_callable=AsyncMock,
            return_value=(None, []),
        ),
        patch(
            "dp.sync.producer.save_sync_plan",
            new_callable=AsyncMock,
        ) as save,
        patch(
            "dp.sync.producer.broker.publish",
            new_callable=AsyncMock,
        ) as publish,
        patch.object(producer, "exit") as exit_app,
    ):
        async with TestApp(producer):
            pass

    save.assert_not_awaited()
    publish.assert_not_awaited()
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_saves_plan_before_publishing_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed run stores its plan and publishes every planned task."""
    config = tmp_path / "sync.json"
    config.write_text('{"tables": []}')
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", config)
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.d.t": "100"},
        paths={"p.d.t": ["s3://bucket/t/data.parquet"]},
    )
    task = SyncTask(
        sync_id="s1",
        bq_table="p.d.t",
        gcs_path="s3://bucket/t/data.parquet",
    )

    with (
        patch(
            "dp.sync.producer.plan_sync",
            new_callable=AsyncMock,
            return_value=(plan, [task]),
        ),
        patch(
            "dp.sync.producer.save_sync_plan",
            new_callable=AsyncMock,
        ) as save,
        patch(
            "dp.sync.producer.broker.publish",
            new_callable=AsyncMock,
        ) as publish,
        patch.object(producer, "exit") as exit_app,
    ):
        async with TestApp(producer):
            pass

    save.assert_awaited_once()
    publish.assert_awaited_once_with(task, stream="dp:sync:tasks")
    exit_app.assert_called_once()
