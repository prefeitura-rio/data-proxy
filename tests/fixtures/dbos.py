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

    async def wait_for_reader_snapshot(schema_name: str, snapshot: int) -> None:
        recorded.names.append("wait_for_reader_snapshot")
        recorded.waited.append((schema_name, snapshot))
        if recorded.wait_error is not None:
            raise recorded.wait_error

    async def record_publish_metrics(result: PublicationResult, name: str) -> None:
        recorded.names.append("record_publish_metrics")

    async def commit_table_state(plan: SyncPlan, result: PublicationResult) -> None:
        recorded.names.append("commit_table_state")

    for step in (
        commit_ducklake_snapshot,
        wait_for_reader_snapshot,
        record_publish_metrics,
        commit_table_state,
    ):
        monkeypatch.setattr(workflows, step.__name__, step)
    return recorded
