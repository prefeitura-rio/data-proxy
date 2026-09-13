"""Tests for current producer planning output."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from duckdb import connect as connect_duckdb
from redis.asyncio import Redis

from dp.constants import DUMP_STREAM, SEED_STREAM
from dp.models import AllSelection, DumpTask, SeedTask, SyncWork
from dp.sync.producer import produce_tasks, producer
from tests.helpers import dump as make_dump
from tests.helpers import sync_plan


@pytest.fixture(autouse=True)
def producer_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dp.utils.clear_s3_bucket", AsyncMock())
    monkeypatch.setattr("dp.sync.producer.ensure_groups", AsyncMock())
    monkeypatch.setattr(
        "dp.sync.producer.connect_duckdb",
        AsyncMock(return_value=connect_duckdb(":memory:")),
    )


pytestmark = pytest.mark.usefixtures("test_settings", "metrics_disabled")


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
        WHEN: produce_tasks runs.
        THEN: the producer application exits.
        """
        with (
            patch(
                "dp.sync.producer.build_sync_work",
                new_callable=AsyncMock,
                return_value=SyncWork([], []),
            ),
            patch.object(producer, "exit") as exit_app,
        ):
            await produce_tasks()

        exit_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_producer_publishes_each_dump_task(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: a sync work with dump tasks.
        WHEN: produce_tasks runs.
        THEN: each dump task is published.
        """
        task = DumpTask(
            run_id="run",
            table="p.d.t",
            bucket_path="s3://b/t",
            selections=[AllSelection()],
        )
        with (
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
            patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
            patch.object(producer, "exit"),
        ):
            await produce_tasks()

        published = publish.call_args.args[0]
        assert published == task
        assert publish.call_args.kwargs["stream"] == DUMP_STREAM

    @pytest.mark.asyncio
    async def test_producer_recovers_run_with_zero_remaining_tasks(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: an active run with zero remaining tasks.
        WHEN: produce_tasks runs.
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
            patch(
                "dp.sync.producer.build_sync_work",
                new_callable=AsyncMock,
                return_value=SyncWork([], []),
            ),
            patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
            patch.object(producer, "exit"),
        ):
            await produce_tasks()

        sleep.assert_awaited_with(60)
        publish.assert_awaited_once_with(SeedTask(run_id="old"), stream=SEED_STREAM)

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
        THEN: it waits before it dispatches work.
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
            patch(
                "dp.sync.producer.build_sync_work",
                new_callable=AsyncMock,
                return_value=work,
            ),
            patch(
                "dp.sync.producer.create_run", new_callable=AsyncMock, return_value=True
            ),
            patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
            patch.object(producer, "exit"),
        ):
            await produce_tasks()

        sleep.assert_awaited_once_with(60)
        publish.assert_awaited_once_with(task, stream="dp:extract")

    @pytest.mark.asyncio
    async def test_producer_publishes_seed_publication_when_no_dumps(
        self,
        sync_config_path: Path,
        redis: Redis,
        broker: object,
    ) -> None:
        """
        GIVEN: a sync work with plans but zero dump tasks.
        WHEN: produce_tasks runs.
        THEN: the producer publishes a seed sync for the run.
        """
        with (
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
            patch("dp.sync.producer.broker.publish", new_callable=AsyncMock) as publish,
            patch.object(producer, "exit"),
        ):
            await produce_tasks()

        publish.assert_awaited_once()
        message = publish.call_args.args[0]
        assert isinstance(message, SeedTask)
        assert message.run_id

    @pytest.mark.asyncio
    async def test_producer_rejects_run_creation_conflict(
        self,
        sync_config_path: Path,
        redis: Redis,
    ) -> None:
        """
        GIVEN: a run creation conflict where create_run returns False.
        WHEN: produce_tasks runs.
        THEN: the producer rejects the new run without publishing dumps.
        """
        with (
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
            await produce_tasks()
