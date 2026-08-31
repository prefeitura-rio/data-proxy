# ruff: noqa: E402
# ruff: noqa: E402
# ruff: noqa: E402
"""Tests for current producer planning output."""

from dp.models import SyncWork


def test_empty_work_has_no_tasks() -> None:
    assert SyncWork(plans=[], tasks=[]).tasks == []


"""Coverage for pipeline applications at external boundaries."""
from pathlib import Path
from unittest.mock import ANY, AsyncMock, call, patch

import pytest

from dp.models import AllSelection, DumpTask, SeedTask
from dp.sync.producer import produce, producer
from tests.helpers import FakeDuckDBConnection, FakeRedis, sync_plan
from tests.helpers import dump as make_dump


@pytest.mark.asyncio
async def test_producer_exits_when_no_changes(path: Path, valkey: FakeRedis) -> None:
    with (
        patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
        patch("dp.sync.producer.connect", return_value=FakeDuckDBConnection()),
        patch(
            "dp.sync.producer.build_sync_work",
            new_callable=AsyncMock,
            return_value=SyncWork([], []),
        ),
        patch.object(producer, "exit") as exit_app,
    ):
        await produce()
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_producer_publishes_dumps(path: Path, valkey: FakeRedis) -> None:
    task = DumpTask(
        run_id="run", table="p.d.t", bucket_path="s3://b/t", selection=AllSelection()
    )
    with (
        patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
        patch("dp.sync.producer.connect", return_value=FakeDuckDBConnection()),
        patch(
            "dp.sync.producer.build_sync_work",
            new_callable=AsyncMock,
            return_value=SyncWork(
                [
                    sync_plan(
                        signatures={"p.d.t": "sig"},
                        paths={"p.d.t": ["s3://b/t/data.parquet"]},
                    )
                ],
                [task],
            ),
        ),
        patch("dp.sync.producer.create_run", new_callable=AsyncMock, return_value=True),
        patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
        patch.object(producer, "exit"),
    ):
        await produce()
    publish.assert_awaited_once_with(task, stream="dp:extract")


@pytest.mark.asyncio
async def test_producer_recovers_zero_remaining_run(
    path: Path, valkey: FakeRedis
) -> None:
    fake = valkey
    fake.store["dp:active"] = "old"
    fake.store["dp:remaining:old"] = "0"
    with (
        patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
        patch.object(producer, "exit"),
    ):
        await produce()
    publish.assert_awaited_once_with(SeedTask(run_id="old"), stream="dp:prepare")


"""Pipeline branch coverage."""


@pytest.mark.asyncio
async def test_producer_refuses_active_run_with_remaining_tasks(
    path: Path,
    valkey: FakeRedis,
) -> None:
    fake = valkey
    fake.store["dp:active"] = "old"
    fake.store["dp:remaining:old"] = "2"
    with (
        patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
        patch.object(producer, "exit"),
    ):
        await produce()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_producer_publishes_seed_for_zero_dumps(
    path: Path, valkey: FakeRedis
) -> None:
    with (
        patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
        patch("dp.sync.producer.connect", return_value=FakeDuckDBConnection()),
        patch(
            "dp.sync.producer.build_sync_work",
            new_callable=AsyncMock,
            return_value=SyncWork(
                [
                    sync_plan(
                        signatures={"p.d.t": "sig"},
                        paths={"p.d.t": ["s3://b/t/data.parquet"]},
                    )
                ],
                [],
            ),
        ),
        patch("dp.sync.producer.create_run", new_callable=AsyncMock, return_value=True),
        patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
        patch.object(producer, "exit"),
    ):
        await produce()
    publish.assert_awaited_once()
    assert publish.await_args == call(ANY, stream="dp:prepare")


@pytest.mark.asyncio
async def test_producer_rejects_run_creation_conflict(
    path: Path,
    valkey: FakeRedis,
) -> None:
    with (
        patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
        patch("dp.sync.producer.connect", return_value=FakeDuckDBConnection()),
        patch(
            "dp.sync.producer.build_sync_work",
            new_callable=AsyncMock,
            return_value=SyncWork(
                [
                    sync_plan(
                        signatures={"p.d.t": "sig"},
                        paths={"p.d.t": ["s3://b/t/data.parquet"]},
                    )
                ],
                [make_dump()],
            ),
        ),
        patch(
            "dp.sync.producer.create_run", new_callable=AsyncMock, return_value=False
        ),
        patch.object(producer, "exit"),
    ):
        await produce()
