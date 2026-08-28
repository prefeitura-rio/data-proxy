"""Tests for the FastStream finalizer orchestrator."""

from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import ANY, AsyncMock, call, patch

import pytest
from faststream.exceptions import StopApplication
from faststream.redis.testing import TestRedisBroker
from helpers import FakeDuckDBConnection, FakePgConn, FakeRedis, FakeRedisCM

from dp.models import FinalizeMessage, PublicationResult, SyncPlan
from dp.sync.finalizer import (
    broker,
    cleanup_finalizer_consumer,
    ensure_consumer_group,
    finalize_sync,
    finalizer,
    subs,
)


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


def test_finalize_subscriptions_read_new_and_reclaim_stale_messages() -> None:
    """New finalization uses group reads and stale work uses auto-claim."""
    assert subs["new"].min_idle_time is None
    assert subs["stale"].min_idle_time == 900_000
    assert subs["new"].consumer != subs["stale"].consumer


@pytest.mark.asyncio
async def test_finalizer_shutdown_cleans_its_consumer() -> None:
    """Finalizer shutdown delegates consumer cleanup to state operations."""
    with patch(
        "dp.sync.finalizer.cleanup_consumer",
        new_callable=AsyncMock,
    ) as cleanup:
        await cleanup_finalizer_consumer()

    cleanup.assert_has_awaits(
        [
            call(ANY, "dp:sync:finalize", "finalizers", subs["new"].consumer),
            call(ANY, "dp:sync:finalize", "finalizers", subs["stale"].consumer),
        ]
    )


@pytest.mark.asyncio
async def test_applies_plan_commits_state_and_exits(
    sync_config_path: Path,
) -> None:
    """Successful orchestration commits an empty plan and exits."""
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
    apply.assert_not_called()
    commit.assert_awaited_once_with(ANY, plan, set())
    exit_app.assert_called_once()


@pytest.mark.asyncio
async def test_loading_failure_stops_application(
    sync_config_path: Path,
) -> None:
    """A later schema failure does not commit shared synchronization state."""
    (sync_config_path.parent / "writers.json").write_text(
        """{
        "writers": {
          "bcadastro": "postgresql://bcadastro-writer",
          "app_pequenos_cariocas": "postgresql://app-writer"
        }
        }"""
    )
    sync_config_path.write_text(
        """{
        "schemas": {
          "bcadastro": {
            "tables": [{"name": "project.dataset.cpf_rio", "strategy": "full"}]
          },
          "app_pequenos_cariocas": {
            "tables": [{"name": "project.dataset.participants", "strategy": "full"}]
          }
        }
        }"""
    )
    plan = SyncPlan(
        sync_id="s1",
        signatures={
            "project.dataset.cpf_rio": "cpf",
            "project.dataset.participants": "participants",
        },
        paths={
            "project.dataset.cpf_rio": ["s3://bucket/cpf"],
            "project.dataset.participants": ["s3://bucket/participants"],
        },
    )

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
                side_effect=[
                    PublicationResult(
                        plan=SyncPlan(
                            sync_id="s1",
                            signatures={"project.dataset.cpf_rio": "cpf"},
                            paths={"project.dataset.cpf_rio": ["s3://bucket/cpf"]},
                        ),
                        published_tables={"project.dataset.cpf_rio"},
                    ),
                    RuntimeError("failed"),
                ],
            ),
            patch(
                "dp.sync.finalizer.commit_sync_state",
                new_callable=AsyncMock,
            ) as commit,
            pytest.raises(StopApplication) as result,
        ):
            await test_broker.publish(
                FinalizeMessage(sync_id="s1"),
                stream="dp:sync:finalize",
            )

    assert result.value.code == 1
    commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_routes_each_schema_plan_to_its_writer(sync_config_path: Path) -> None:
    """One finalizer message uses the configured writer for each schema."""
    (sync_config_path.parent / "writers.json").write_text(
        """{
        "writers": {
          "bcadastro": "postgresql://bcadastro-writer",
          "app_pequenos_cariocas": "postgresql://app-writer"
        }
        }"""
    )
    sync_config_path.write_text(
        """{
        "schemas": {
          "bcadastro": {
            "tables": [{"name": "project.dataset.cpf_rio", "strategy": "full"}]
          },
          "app_pequenos_cariocas": {
            "tables": [{"name": "project.dataset.participants", "strategy": "full"}]
          }
        }
        }"""
    )
    plan = SyncPlan(
        sync_id="s1",
        signatures={
            "project.dataset.cpf_rio": "cpf",
            "project.dataset.participants": "participants",
        },
        paths={
            "project.dataset.cpf_rio": ["s3://bucket/cpf"],
            "project.dataset.participants": ["s3://bucket/participants"],
        },
    )

    def publish(
        _: object,
        __: object,
        ___: object,
        schema_plan: SyncPlan,
        ____: object,
    ) -> PublicationResult:
        return PublicationResult(
            plan=schema_plan,
            published_tables=set(schema_plan.signatures),
        )

    with (
        patch_make_redis(),
        patch(
            "dp.sync.finalizer.read_sync_plan",
            new_callable=AsyncMock,
            return_value=plan,
        ),
        patch(
            "dp.sync.finalizer.psycopg.connect", return_value=FakePgConn()
        ) as connect,
        patch("dp.sync.finalizer.connect", return_value=FakeDuckDBConnection()),
        patch("dp.sync.finalizer.apply_sync_plan", side_effect=publish),
        patch("dp.sync.finalizer.commit_sync_state", new_callable=AsyncMock) as commit,
        patch("dp.sync.finalizer.broker.publish", new_callable=AsyncMock),
        patch.object(finalizer, "exit"),
    ):
        await finalize_sync(FinalizeMessage(sync_id="s1"))

    assert connect.call_args_list == [
        call("postgresql://bcadastro-writer"),
        call("postgresql://app-writer"),
    ]
    commit.assert_awaited_once_with(
        ANY,
        plan,
        {"project.dataset.cpf_rio", "project.dataset.participants"},
    )
