from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from dbos import DBOS

from sync.constants import SYNC_QUEUE
from sync.dbos import workflows
from sync.models import (
    LoadedConfig,
    PublicationResult,
    SyncPlan,
    SyncScope,
    SyncWork,
)
from sync.settings import settings
from tests.fixtures.types import Calls


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Replace the publish workflow steps with recorders."""
    recorded = Calls()

    async def commit_ducklake_snapshot(
        plan: SyncPlan, failed_paths: set[str], document: str
    ) -> PublicationResult:
        recorded.names.append("commit_ducklake_snapshot")
        return PublicationResult(
            plan=plan, published_tables=set(), snapshot_id=recorded.snapshot_id
        )

    async def record_publish_metrics(result: PublicationResult, name: str) -> None:
        recorded.names.append("record_publish_metrics")

    async def commit_table_state(
        plan: SyncPlan, result: PublicationResult, document: str
    ) -> None:
        recorded.names.append("commit_table_state")

    async def apply_ducklake_maintenance(schema_name: str, document: str) -> int:
        recorded.names.append("apply_ducklake_maintenance")
        if recorded.maintenance_error is not None:
            raise recorded.maintenance_error
        return recorded.maintained_snapshot_id

    for step in (
        commit_ducklake_snapshot,
        record_publish_metrics,
        commit_table_state,
        apply_ducklake_maintenance,
    ):
        monkeypatch.setattr(workflows, step.__name__, step)
    return recorded


@pytest.fixture
async def sync_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[list[SyncScope]]:
    """Run a real DBOS worker on the sync queue and record the scope of each run."""
    scopes: list[SyncScope] = []

    async def build_sync_work(run_id: str, document: str, scope: SyncScope) -> SyncWork:
        scopes.append(scope)
        return SyncWork(plans=[], tasks=[])

    monkeypatch.setattr(
        workflows,
        "load_sync_config",
        AsyncMock(return_value=LoadedConfig(revision="1", document="{}")),
    )
    monkeypatch.setattr(workflows, "build_sync_work", build_sync_work)
    DBOS(
        config={
            "name": settings.DBOS_APPLICATION_NAME,
            "application_version": "test",
            "system_database_url": settings.DBOS_SYSTEM_DATABASE_URL,
        }
    )
    DBOS.listen_queues([SYNC_QUEUE])
    DBOS.launch()
    await DBOS.register_queue_async(SYNC_QUEUE, on_conflict="never_update")
    try:
        yield scopes
    finally:
        DBOS.destroy(destroy_registry=False)
