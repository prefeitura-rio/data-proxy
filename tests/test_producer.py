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

pytestmark = pytest.mark.usefixtures("test_settings", "mock_push_to_gateway")


class TestProducer:
    """Tests for producer planning and dispatch behavior."""

    def test_empty_sync_work_has_no_tasks(
        self,
    ) -> None:
        """
        GIVEN: an empty SyncWork.
        WHEN: its tasks are accessed.
        THEN: there are no tasks.
        """
        assert SyncWork(plans=[], tasks=[]).tasks == []

    @pytest.mark.asyncio
    async def test_producer_exits_when_no_changes_detected(
        self, sync_config_path: Path, redis: Redis
    ) -> None:
        """
        GIVEN: no changes detected by build_sync_work.
        WHEN: produce runs.
        THEN: the producer application exits.
        """
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
    async def test_producer_clears_bucket_before_publishing_tasks(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: a sync work with dump tasks.
        WHEN: produce runs.
        THEN: clear_bucket is called before tasks are published.
        """
        task = DumpTask(
            run_id="run",
            table="p.d.t",
            bucket_path="s3://b/t",
            selection=AllSelection(),
        )
        call_order: list[str] = []

        async def record_clear() -> None:
            call_order.append("clear")

        async def record_publish(*args: object, **kwargs: object) -> None:
            call_order.append("publish")

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
            patch("dp.sync.producer.clear_bucket", side_effect=record_clear),
            patch("dp.sync.producer.broker.publish", side_effect=record_publish),
            patch.object(producer, "exit"),
        ):
            await produce()

        assert call_order[0] == "clear"

    @pytest.mark.asyncio
    async def test_producer_publishes_each_dump_task(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: a sync work with dump tasks.
        WHEN: produce runs.
        THEN: each dump task is published.
        """
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
            patch("dp.sync.producer.clear_bucket", new_callable=AsyncMock),
            patch("dp.sync.dumper.extract_task"),
            patch(
                "dp.sync.dumper.complete_dump", new_callable=AsyncMock, return_value=1
            ),
            patch.object(dumper, "exit"),
            patch.object(producer, "exit"),
        ):
            await produce()
        assert dump_task.mock.call_count == 1
        dump_task.mock.assert_called_with(task.model_dump(mode="json"))

    @pytest.mark.asyncio
    async def test_producer_recovers_run_with_zero_remaining_tasks(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: an active run with zero remaining tasks.
        WHEN: produce runs.
        THEN: the producer recovers the run and publishes a seed sync.
        """
        with (
            patch(
                "dp.sync.producer.read_active_run",
                new_callable=AsyncMock,
                side_effect=["old", "old", None],
            ),
            patch(
                "dp.sync.producer.read_remaining",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("dp.sync.producer.sleep", new_callable=AsyncMock) as sleep,
            patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
            patch("dp.sync.producer.connect", return_value=connect(":memory:")),
            patch(
                "dp.sync.producer.build_sync_work",
                new_callable=AsyncMock,
                return_value=SyncWork([], []),
            ),
            patch.object(producer, "exit"),
        ):
            await produce()

        sleep.assert_awaited_with(60)
        assert seed_sync.mock.call_count == 1
        seed_sync.mock.assert_called_with({"run_id": "old"})

    @pytest.mark.asyncio
    async def test_producer_waits_for_active_pipeline_before_dispatch(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: an active pipeline that later completes.
        WHEN: the next Producer runs.
        THEN: it waits before it clears artifacts and dispatches work.
        """
        task = make_dump()
        work = SyncWork(
            [
                sync_plan(
                    signatures={"p.d.t": "sig"},
                    paths={"p.d.t": ["s3://b/t/data.parquet"]},
                )
            ],
            [task],
        )
        with (
            patch(
                "dp.sync.producer.read_active_run",
                new_callable=AsyncMock,
                side_effect=["old", None],
            ),
            patch(
                "dp.sync.producer.read_remaining",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("dp.sync.producer.sleep", new_callable=AsyncMock) as sleep,
            patch("dp.sync.producer.ensure_groups", new_callable=AsyncMock),
            patch("dp.sync.producer.connect", return_value=connect(":memory:")),
            patch(
                "dp.sync.producer.build_sync_work",
                new_callable=AsyncMock,
                return_value=work,
            ),
            patch(
                "dp.sync.producer.create_run", new_callable=AsyncMock, return_value=True
            ),
            patch("dp.sync.producer.clear_bucket", new_callable=AsyncMock) as clear,
            patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
            patch.object(producer, "exit"),
        ):
            await produce()

        sleep.assert_awaited_once_with(60)
        clear.assert_awaited_once()
        publish.assert_awaited_once_with(task, stream="dp:extract")

    @pytest.mark.asyncio
    async def test_producer_publishes_seed_sync_when_no_dumps(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: a sync work with plans but zero dump tasks.
        WHEN: produce runs.
        THEN: the producer publishes a seed sync for the run.
        """
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
            patch("dp.sync.producer.clear_bucket", new_callable=AsyncMock),
            patch.object(producer, "exit"),
        ):
            await produce()
        assert seed_sync.mock.call_count == 1
        assert all(call.args[0]["run_id"] for call in seed_sync.mock.call_args_list)

    @pytest.mark.asyncio
    async def test_producer_rejects_run_creation_conflict(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a run creation conflict where create_run returns False.
        WHEN: produce runs.
        THEN: the producer rejects the new run without publishing dumps.
        """
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
