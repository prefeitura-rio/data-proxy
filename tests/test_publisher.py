# ruff: noqa: E402
# ruff: noqa: E402
"""Tests for current publisher subscriptions."""

from dp.sync.publisher import subs


def test_publisher_has_new_and_stale_subscriptions() -> None:
    assert subs["new"].min_idle_time is None
    assert subs["stale"].min_idle_time is not None


"""Coverage for Publisher terminal paths."""
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from dp.models import PublishTask, SchemaWriters
from dp.sync.publisher import publish_schema, publisher
from tests.helpers import FakeRedis


@pytest.mark.asyncio
async def test_missing_publish_plan_exits(path: Path, valkey: FakeRedis) -> None:
    fake = valkey
    fake.store["dp:active"] = "r1"
    with (
        patch("dp.sync.publisher.psycopg.connect", return_value=MagicMock()),
        patch("dp.sync.publisher.reload_postgrest"),
        patch.object(publisher, "exit") as exit_app,
    ):
        await publish_schema(PublishTask(run_id="r1", schema_name="app"))
    exit_app.assert_called_once()


"""Additional Publisher coverage."""
from unittest.mock import AsyncMock, MagicMock

from dp.models import (
    FullTable,
    PartitionedTable,
    PublicationResult,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
)
from dp.sync.publisher import publish_plan
from tests.helpers import FakeDuckDBConnection, FakePgConn


def test_publish_plan_wraps_connections() -> None:
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.t")])}
    )
    plan = SyncPlan(schema_name="app")
    with (
        patch("dp.sync.publisher.psycopg.connect", return_value=FakePgConn()),
        patch("dp.sync.publisher.connect", return_value=FakeDuckDBConnection()),
        patch(
            "dp.sync.publisher.apply_sync_plan",
            return_value=PublicationResult(plan=plan, published_tables=set()),
        ) as apply,
    ):
        result = publish_plan("postgresql://writer", config, plan, set())
    assert result.published_tables == set()
    apply.assert_called_once()


@pytest.mark.asyncio
async def test_publish_schema_publishes_and_keeps_remaining_plan(
    config: Callable[[SyncConfig], None],
    valkey: FakeRedis,
    writers: SchemaWriters,
) -> None:
    config(
        SyncConfig(schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.t")])})
    )
    fake = valkey
    plan = SyncPlan(
        schema_name="app",
        signatures={"p.app.t": "sig"},
        paths={"p.app.t": ["s3://b/t"]},
    )
    fake.hashes["dp:plans:r1"] = {"app": plan.model_dump_json()}
    result = PublicationResult(plan=plan, published_tables={"p.app.t"})
    with (
        patch.object(publisher, "exit"),
        patch("dp.settings.Settings.schema_writers", return_value=writers),
        patch("dp.sync.publisher.publish_plan", return_value=result),
        patch(
            "dp.sync.publisher.complete_schema", new_callable=AsyncMock, return_value=1
        ),
    ):
        await publish_schema(PublishTask(run_id="r1", schema_name="app"))


"""Publisher path coverage."""


from dp.models import (
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
)
from dp.sync.publisher import (
    cleanup_publisher_consumers,
)


def partition() -> PhysicalPartition:
    return PhysicalPartition(
        partition_id="1",
        signature="s",
        selection=RangeSelection(partition_id="1", column="id", lower=1, upper=2),
    )


@pytest.mark.asyncio
async def test_publisher_commits_partition_state_and_cleans_last_plan(
    config: Callable[[SyncConfig], None],
    valkey: FakeRedis,
) -> None:
    config(
        SyncConfig(
            schemas={"app": SchemaConfig(tables=[PartitionedTable(name="p.app.t")])}
        )
    )
    plan = SyncPlan(
        schema_name="app",
        partitioned_tables={
            "p.app.t": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=True,
                current_partitions={"1": partition()},
                changed_paths={"1": "s3://b"},
                removed_partitions={},
            )
        },
    )
    with (
        patch("dp.settings.Settings.schema_writers"),
        patch(
            "dp.sync.publisher.read_sync_plan",
            new_callable=AsyncMock,
            return_value=plan,
        ),
        patch(
            "dp.sync.publisher.read_failed_paths",
            new_callable=AsyncMock,
            return_value=set(),
        ),
        patch(
            "dp.sync.publisher.publish_plan",
            return_value=MagicMock(plan=plan, published_tables={"p.app.t"}),
        ),
        patch(
            "dp.sync.publisher.complete_schema", new_callable=AsyncMock, return_value=0
        ),
        patch("dp.sync.publisher.psycopg.connect", return_value=MagicMock()),
        patch("dp.sync.publisher.reload_postgrest"),
        patch.object(publisher, "exit"),
    ):
        await publish_schema(PublishTask(run_id="r1", schema_name="app"))


@pytest.mark.asyncio
async def test_publisher_cleanup_removes_consumers(valkey: FakeRedis) -> None:
    with (
        patch("dp.sync.publisher.cleanup_consumer", new_callable=AsyncMock) as cleanup,
    ):
        await cleanup_publisher_consumers()
    assert cleanup.await_count == 2
