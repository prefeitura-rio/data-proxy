"""Tests for current producer planning output."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from duckdb import connect
from redis.asyncio import Redis

from dp.models import AllSelection, DumpTask, SyncWork
from dp.sync.dumper import dump_task, dumper
from dp.sync.producer import produce, producer
from dp.sync.seeder import seed_sync
from tests.helpers import dump as make_dump
from tests.helpers import sync_plan


class TestProducer:
    """Tests for producer planning and dispatch behavior."""

    def test_empty_work_has_no_tasks(
        self,
    ) -> None:
        """Verify empty work has no tasks."""
        assert SyncWork(plans=[], tasks=[]).tasks == []

    @pytest.mark.asyncio
    async def test_producer_exits_when_no_changes(
        self, sync_config_path: Path, redis: Redis
    ) -> None:
        """Verify producer exits when no changes."""
        with (
            patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
            patch("dp.sync.producer.connect", return_value=connect(":memory:")),
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
    async def test_producer_publishes_dumps(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """Verify producer publishes dumps."""
        task = DumpTask(
            run_id="run",
            table="p.d.t",
            bucket_path="s3://b/t",
            selection=AllSelection(),
        )
        with (
            patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
            patch("dp.sync.producer.connect", return_value=connect(":memory:")),
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
            patch(
                "dp.sync.producer.create_run", new_callable=AsyncMock, return_value=True
            ),
            patch("dp.sync.dumper.extract_task_wrapper"),
            patch(
                "dp.sync.dumper.complete_dump", new_callable=AsyncMock, return_value=1
            ),
            patch.object(dumper, "exit"),
            patch.object(producer, "exit"),
        ):
            await produce()
        assert dump_task.mock.call_count == 2
        dump_task.mock.assert_called_with(task.model_dump(mode="json"))

    @pytest.mark.asyncio
    async def test_producer_recovers_zero_remaining_run(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """Verify producer recovers zero remaining run."""
        fake = redis
        await fake.set("dp:active", "old")
        await fake.set("dp:remaining:old", "0")
        with patch.object(producer, "exit"):
            await produce()
        assert seed_sync.mock.call_count == 2
        seed_sync.mock.assert_called_with({"run_id": "old"})

    """Pipeline branch coverage."""

    @pytest.mark.asyncio
    async def test_producer_refuses_active_run_with_remaining_tasks(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """Verify producer refuses active run with remaining tasks."""
        fake = redis
        await fake.set("dp:active", "old")
        await fake.set("dp:remaining:old", "2")
        with patch.object(producer, "exit"):
            await produce()
        assert not seed_sync.mock.called

    @pytest.mark.asyncio
    async def test_producer_publishes_seed_for_zero_dumps(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """Verify producer publishes seed for zero dumps."""
        with (
            patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
            patch("dp.sync.producer.connect", return_value=connect(":memory:")),
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
            patch(
                "dp.sync.producer.create_run", new_callable=AsyncMock, return_value=True
            ),
            patch.object(producer, "exit"),
        ):
            await produce()
        assert seed_sync.mock.call_count == 2
        assert all(call.args[0]["run_id"] for call in seed_sync.mock.call_args_list)

    @pytest.mark.asyncio
    async def test_producer_rejects_run_creation_conflict(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """Verify producer rejects run creation conflict."""
        with (
            patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
            patch("dp.sync.producer.connect", return_value=connect(":memory:")),
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
                "dp.sync.producer.create_run",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(producer, "exit"),
        ):
            await produce()
