from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import override
from unittest.mock import AsyncMock

import pytest
from dbos import DBOS

from sync.constants import PROXY_QUEUE, SYNC_QUEUE
from sync.dbos import workflows
from sync.models import (
    LoadedConfig,
    PublicationResult,
    SyncPlan,
    SyncScope,
    SyncWork,
)
from sync.settings import settings
from tests.constants import POSTGRES_URL
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
    monkeypatch.setattr(settings, "DBOS_SYSTEM_DATABASE_URL", f"{POSTGRES_URL}/test")
    DBOS(
        config={
            "name": settings.DBOS_APPLICATION_NAME,
            "application_version": "test",
            "system_database_url": f"{POSTGRES_URL}/test",
        }
    )
    DBOS.listen_queues([SYNC_QUEUE, PROXY_QUEUE])
    DBOS.launch()
    for queue in (SYNC_QUEUE, PROXY_QUEUE):
        await DBOS.register_queue_async(queue, on_conflict="never_update")
    try:
        yield scopes
    finally:
        DBOS.destroy(destroy_registry=False)


@pytest.fixture
def proxy_admin(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    """Serve a proxy admin port on a loopback port and record each request that it gets."""
    received: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        """Answer every POST like a proxy that reloaded."""

        def do_POST(self) -> None:
            """Record the request and say that the settings changed."""
            received.append((self.command, self.path))
            payload = b'{"changed":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        @override
        def log_message(self, format: str, *args: object) -> None:
            """Keep the test output quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    monkeypatch.setattr(
        settings, "PROXY_ADMIN_URL", f"http://127.0.0.1:{server.server_port}/reload"
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield received
    server.shutdown()
    server.server_close()
