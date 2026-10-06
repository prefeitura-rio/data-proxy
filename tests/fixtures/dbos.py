"""DBOS workflow fixtures."""

import pytest

from data_proxy.dbos import workflows
from data_proxy.models import PublicationResult, SyncPlan
from tests.fixtures.types import Calls


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Replace the publish workflow steps with recorders."""
    recorded = Calls()

    async def commit_ducklake_snapshot(
        plan: SyncPlan, failed_paths: set[str]
    ) -> PublicationResult:
        recorded.names.append("commit_ducklake_snapshot")
        return PublicationResult(
            plan=plan, published_tables=set(), snapshot_id=recorded.snapshot_id
        )

    async def record_publish_metrics(result: PublicationResult, name: str) -> None:
        recorded.names.append("record_publish_metrics")

    async def commit_table_state(plan: SyncPlan, result: PublicationResult) -> None:
        recorded.names.append("commit_table_state")

    async def apply_ducklake_maintenance(schema_name: str) -> int:
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
