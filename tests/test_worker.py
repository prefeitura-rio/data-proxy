"""Tests for dp.sync.worker."""

from unittest.mock import AsyncMock, patch

import pytest
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection, FakeRedis, FakeRedisCM

from dp.constants import SYNC_TASKS_STREAM
from dp.sync.models import SyncTask
from dp.sync.worker import broker, build_mapping, process_shard

MSG_DUMP = SyncTask(sync_id="s1", bq_table="p.d.t", gcs_path="gs://b/t/data.parquet")
MSG_WINDOW = SyncTask(
    sync_id="s1",
    bq_table="p.d.t",
    gcs_path="gs://b/t/2025-01-15/data.parquet",
    partition_column="dt",
    partition_value="2025-01-15",
)


class TestBuildMapping:
    @pytest.mark.parametrize(
        ("msg", "expected_template", "expected_subset"),
        [
            (
                MSG_DUMP,
                "duckdb/write_dump",
                {"bq_table": "p.d.t", "gcs_path": "gs://b/t/data.parquet"},
            ),
            (
                MSG_WINDOW,
                "duckdb/write_window",
                {"partition_column": "dt", "partition_value": "2025-01-15"},
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
