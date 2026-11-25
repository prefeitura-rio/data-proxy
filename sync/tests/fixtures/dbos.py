from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from sync.dbos import main as dbos_main
from sync.dbos import steps, workflows
from sync.duckdb import DuckDB
from sync.models import (
    PublicationResult,
    SyncPlan,
)
from sync.postgres import Postgres
from tests.fixtures.types import (
    Calls,
    Counted,
    RecordedOptions,
    RecordingDbos,
    ReturnsAtOnce,
)

CONNECTION = object()


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

    async def apply_ducklake_maintenance(schema_name: str, document: str) -> int:
        recorded.names.append("apply_ducklake_maintenance")
        if recorded.maintenance_error is not None:
            raise recorded.maintenance_error
        return recorded.maintained_snapshot_id

    for step in (commit_ducklake_snapshot, apply_ducklake_maintenance):
        monkeypatch.setattr(workflows, step.__name__, step)
    return recorded


@pytest.fixture
def step_counters(monkeypatch: pytest.MonkeyPatch) -> dict[str, Counted]:
    """Replace the counters of the steps, and the connections that the steps open."""
    counters = {
        name: Counted()
        for name in ("dump_tasks_total", "publish_tables_total", "seed_runs_total")
    }
    for name, counted in counters.items():
        monkeypatch.setattr(steps.metrics, name, counted)

    @asynccontextmanager
    async def connect(*_: object) -> AsyncGenerator[object]:
        yield object()

    monkeypatch.setattr(DuckDB, "connect", connect)
    monkeypatch.setattr(Postgres, "connect", connect)
    return counters


@pytest.fixture
def proxy_standbys(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stand in for the database and return the wait for the standbys."""

    @asynccontextmanager
    async def connect(dsn: str) -> AsyncGenerator[object]:
        yield CONNECTION

    wait = AsyncMock()
    monkeypatch.setattr(Postgres, "connect", connect)
    monkeypatch.setattr(steps, "wait_for_standbys", wait)
    return wait


@pytest.fixture
def recording_dbos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace DBOS, and reset what it recorded."""
    RecordingDbos.queues = []
    RecordingDbos.concurrency = {}
    RecordingDbos.enqueued = []
    RecordingDbos.listened = []
    RecordingDbos.deduplicated = False
    RecordingDbos.options = []
    RecordingDbos.destroyed = []
    monkeypatch.setattr(dbos_main, "DBOS", RecordingDbos)
    monkeypatch.setattr(dbos_main, "SetEnqueueOptions", RecordedOptions)
    monkeypatch.setattr(dbos_main, "Event", ReturnsAtOnce)


@pytest.fixture
def dump_failure_record(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stand in for the failure record, and return it."""
    record = AsyncMock()
    monkeypatch.setattr(workflows, "record_dump_failure", record)

    return record


@pytest.fixture
def stored_sync_config(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stand in for the database and return the reader of the stored configuration."""

    @asynccontextmanager
    async def connect(dsn: str) -> AsyncGenerator[None]:
        yield

    read = AsyncMock(return_value=None)
    monkeypatch.setattr(Postgres, "connect", connect)
    monkeypatch.setattr(steps, "read_sync_config", read)
    return read
