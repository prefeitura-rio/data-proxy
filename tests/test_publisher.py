# ruff: noqa: E402
# ruff: noqa: E402
"""Tests for current publisher subscriptions."""

from dp.sync.publisher import subs


def test_publisher_has_new_and_stale_subscriptions() -> None:
    assert subs["new"].min_idle_time is None
    assert subs["stale"].min_idle_time is not None


"""Coverage for Publisher terminal paths."""
from pathlib import Path
from unittest.mock import patch

import pytest
from duckdb import connect
from redis.asyncio import Redis

from dp.models import PublishTask
from dp.sync.publisher import publish_schema, publisher
from dp.sync.seeder import broker as seeder_broker


@pytest.mark.asyncio
async def test_missing_publish_plan_exits(sync_config_path: Path, redis: Redis) -> None:
    fake = redis
    await fake.set("dp:active", "r1")
    with (
        patch("dp.sync.publisher.psycopg.connect", return_value=MagicMock()),
        patch("dp.sync.publisher.reload_postgrest"),
        patch.object(publisher, "exit") as exit_app,
    ):
        await publish_schema(PublishTask(run_id="r1", schema_name="app"))
    exit_app.assert_called_once()


"""Additional Publisher coverage."""
from unittest.mock import AsyncMock, MagicMock

from psycopg import Connection

from dp.models import (
    FullTable,
    PartitionedTable,
    PublicationResult,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
)
from dp.sync.publisher import publish_plan


def test_publish_plan_wraps_connections(
    postgres: Connection[tuple[object, ...]],
) -> None:
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.t")])}
    )
    plan = SyncPlan(schema_name="app")
    with (
        patch("dp.sync.publisher.psycopg.connect", return_value=postgres),
        patch("dp.sync.publisher.connect", return_value=connect(":memory:")),
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
    sync_config_path: Path,
    redis: Redis,
    broker: object,
) -> None:
    sync_config_path.write_text(
        SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.t")])}
        ).model_dump_json()
    )
    fake = redis
    plan = SyncPlan(
        schema_name="app",
        signatures={"p.app.t": "sig"},
        paths={"p.app.t": ["s3://b/t"]},
    )
    await fake.hset("dp:plans:r1", "app", plan.model_dump_json())
    result = PublicationResult(plan=plan, published_tables={"p.app.t"})
    with (
        patch.object(publisher, "exit"),
        patch("dp.sync.publisher.publish_plan", return_value=result),
        patch(
            "dp.sync.publisher.complete_schema", new_callable=AsyncMock, return_value=1
        ),
    ):
        await seeder_broker.publish(
            PublishTask(run_id="r1", schema_name="app"), stream="dp:publish"
        )
    assert publish_schema.mock.call_count == 2


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
    sync_config_path: Path,
    redis: Redis,
) -> None:
    sync_config_path.write_text(
        SyncConfig(
            schemas={"app": SchemaConfig(tables=[PartitionedTable(name="p.app.t")])}
        ).model_dump_json()
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
async def test_publisher_cleanup_removes_consumers(redis: Redis) -> None:
    with (
        patch("dp.sync.publisher.cleanup_consumer", new_callable=AsyncMock) as cleanup,
    ):
        await cleanup_publisher_consumers()
    assert cleanup.await_count == 2
