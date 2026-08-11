"""Tests for dp.sync.worker."""

from unittest.mock import AsyncMock, patch

import pytest
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection, FakeRedis, FakeRedisCM

from dp.constants import SYNC_SHUTDOWN_CHANNEL, SYNC_TASKS_STREAM
from dp.settings import settings
from dp.sync.models import ShutdownMessage, SyncTask
from dp.sync.worker import broker, build_columns, build_mapping, process_shard, worker

MSG_DUMP = SyncTask(sync_id="s1", bq_table="p.d.t", gcs_path="gs://b/t/data.parquet")
MSG_WINDOW = SyncTask(
    sync_id="s1",
    bq_table="p.d.t",
    gcs_path="gs://b/t/2025-01-15/data.parquet",
    partition_column="dt",
    partition_value="2025-01-15",
)


class TestBuildColumns:
    def test_returns_star_when_no_json_columns(self) -> None:
        """A bare '*' is returned when the json_columns list is empty."""
        result = build_columns([])

        assert result == "*"

    def test_single_json_column(self) -> None:
        """A single STRUCT column produces a REPLACE clause with one to_json() call."""
        result = build_columns(["id_cras_list"])

        assert result == '* REPLACE (to_json("id_cras_list") AS "id_cras_list")'

    def test_multiple_json_columns(self) -> None:
        """Multiple STRUCT columns are each wrapped in to_json() within one REPLACE clause."""
        result = build_columns(["id_cras_list", "dados"])

        assert result == (
            '* REPLACE (to_json("id_cras_list") AS "id_cras_list", '
            'to_json("dados") AS "dados")'
        )


class TestBuildMapping:
    @pytest.mark.parametrize(
        ("msg", "expected_template", "expected_subset"),
        [
            (
                MSG_DUMP,
                "duckdb/write_dump",
                {
                    "bq_table": "p.d.t",
                    "gcs_path": "gs://b/t/data.parquet",
                    "columns":  "*",
                },
            ),
            (
                MSG_WINDOW,
                "duckdb/write_window",
                {
                    "partition_column": "dt",
                    "partition_value":  "2025-01-15",
                    "columns":          "*",
                },
            ),
        ],
        ids=["dump", "window"],
    )
    def test_build_mapping(
        self,
        msg: SyncTask,
        expected_template: str,
        expected_subset: dict[str, str],
    ) -> None:
        """Template name and mapping keys match the task type."""
        template, mapping = build_mapping(msg)
        assert template == expected_template
        assert expected_subset.items() <= mapping.items()

    def test_mapping_with_json_columns(self) -> None:
        """STRUCT columns produce a REPLACE expression in the columns key."""
        msg = SyncTask(
            sync_id="s1",
            bq_table="p.d.t",
            gcs_path="gs://b/t/data.parquet",
            json_columns=["id_cras_list"],
        )
        _, mapping = build_mapping(msg)

        assert mapping["columns"] == '* REPLACE (to_json("id_cras_list") AS "id_cras_list")'


class TestProcessShard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("msg", [MSG_DUMP, MSG_WINDOW], ids=["dump", "window"])
    async def test_executes_sql(self, msg: SyncTask) -> None:
        """Subscriber routes the task to process_shard and executes the rendered SQL."""
        db = FakeDuckDBConnection()
        async with TestRedisBroker(broker) as br:
            with (
                patch("dp.sync.worker.connect", return_value=db),
                patch("dp.sync.worker.load_template", return_value="SELECT 1"),
                patch(
                    "dp.settings.Settings.make_redis",
                    return_value=FakeRedisCM(FakeRedis()),
                ),
            ):
                await br.publish(msg, stream=SYNC_TASKS_STREAM)

        assert db.executed == ["SELECT 1"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("decr_value", "expected_calls"),
        [(0, 1), (1, 0)],
        ids=["remaining-zero", "remaining-nonzero"],
    )
    async def test_finalize_published_when_counter_zero(
        self, decr_value: int, expected_calls: int
    ) -> None:
        """FinalizeMessage is published only when the job counter reaches zero."""
        with (
            patch("dp.sync.worker.connect", return_value=FakeDuckDBConnection()),
            patch("dp.sync.worker.load_template", return_value="SELECT 1"),
            patch(
                "dp.settings.Settings.make_redis",
                return_value=FakeRedisCM(FakeRedis(decr_value=decr_value)),
            ),
            patch("dp.sync.worker.broker.publish", new_callable=AsyncMock) as mock_pub,
        ):
            await process_shard(MSG_DUMP)

        assert mock_pub.call_count == expected_calls


class TestInactivityExit:
    @pytest.mark.asyncio
    async def test_exits_when_queue_empty(self) -> None:
        with (
            patch("dp.sync.worker.connect", return_value=FakeDuckDBConnection()),
            patch("dp.sync.worker.load_template", return_value="SELECT 1"),
            patch(
                "dp.settings.Settings.make_redis",
                return_value=FakeRedisCM(FakeRedis(decr_value=1, lag=0)),
            ),
            patch("dp.sync.worker.broker.publish", new_callable=AsyncMock),
            patch("dp.sync.worker.asyncio.sleep") as mock_sleep,
            patch.object(worker, "exit") as mock_exit,
        ):
            await process_shard(MSG_DUMP)

        mock_sleep.assert_awaited_once_with(settings.WORKER_INACTIVITY_TIMEOUT)
        mock_exit.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_exit_when_queue_has_lag(self) -> None:
        with (
            patch("dp.sync.worker.connect", return_value=FakeDuckDBConnection()),
            patch("dp.sync.worker.load_template", return_value="SELECT 1"),
            patch(
                "dp.settings.Settings.make_redis",
                return_value=FakeRedisCM(FakeRedis(decr_value=1, lag=5)),
            ),
            patch("dp.sync.worker.broker.publish", new_callable=AsyncMock),
            patch("dp.sync.worker.asyncio.sleep") as mock_sleep,
            patch.object(worker, "exit") as mock_exit,
        ):
            await process_shard(MSG_DUMP)

        mock_sleep.assert_not_called()
        mock_exit.assert_not_called()


class TestHandleShutdown:
    @pytest.mark.asyncio
    async def test_exits_on_finalize_message(self) -> None:
        async with TestRedisBroker(broker) as br:
            with patch.object(worker, "exit") as mock_exit:
                await br.publish(ShutdownMessage(sync_id="s1"), SYNC_SHUTDOWN_CHANNEL)

        mock_exit.assert_called_once()
