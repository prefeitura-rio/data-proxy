from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, cast

from dbos import StepInfo, WorkflowStatus
from httpx2 import ASGITransport, AsyncClient
from psycopg_pool import PoolTimeout
from whenever import Instant

from console.dependencies import get_client, get_pool
from console.main import app

NOW_MS = 1_800_000_000_000


def workflow(**fields: object) -> WorkflowStatus:
    """Build a DBOS workflow status of a finished scheduled sync run."""
    status = WorkflowStatus()
    values: dict[str, object] = {
        "workflow_id": "run-1",
        "status": "SUCCESS",
        "name": "run_sync",
        "queue_name": "sync",
        "schedule_name": "sync-schedule",
        "parent_workflow_id": None,
        "created_at": NOW_MS,
        "dequeued_at": NOW_MS + 1_000,
        "completed_at": NOW_MS + 61_000,
        "error": None,
    }
    for name, value in (values | fields).items():
        setattr(status, name, value)
    return status


def at(milliseconds: int) -> str:
    """Return the ISO text that the console writes for a DBOS timestamp."""
    return Instant.from_timestamp_millis(milliseconds).format_iso()


@dataclass
class FakeQueue:
    """A DBOS queue that holds its configuration in memory."""

    name: str
    global_concurrency: int | None = None
    worker_concurrency: int | None = None
    limiter: dict[str, float] | None = None
    partition_concurrency: int | None = None
    partition_worker_concurrency: int | None = None
    partition_limiter: dict[str, float] | None = None
    polling_interval_sec: float = 1.0


def schedule(**fields: object) -> dict[str, object]:
    """Build the DBOS record of the nightly sync schedule."""
    values: dict[str, object] = {
        "schedule_id": "schedule-1",
        "schedule_name": "sync",
        "workflow_name": "run_sync",
        "workflow_class_name": None,
        "schedule": "0 2 * * *",
        "status": "ACTIVE",
        "context": None,
        "last_fired_at": None,
        "automatic_backfill": False,
        "cron_timezone": None,
        "queue_name": "sync",
        "application_name": None,
    }
    return values | fields


@dataclass
class FakeClient:
    """A DBOS client that answers from memory."""

    connected: bool = True
    workflows: list[WorkflowStatus] = field(default_factory=list)
    children: dict[str, list[WorkflowStatus]] = field(default_factory=dict)
    steps: dict[str, list[StepInfo]] = field(default_factory=dict)
    queues: list[FakeQueue] = field(default_factory=list)
    queued: list[WorkflowStatus] = field(default_factory=list)
    schedules: list[dict[str, object]] = field(default_factory=list)
    streams: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    calls: list[dict[str, object]] = field(default_factory=list)

    async def list_workflows_async(self, **filters: object) -> list[WorkflowStatus]:
        """Record the filters and answer like DBOS: by id, by parent, or all."""
        self.calls.append(filters)
        if (parent := filters.get("parent_workflow_id")) is not None:
            return self.children.get(str(parent), [])
        if "workflow_ids" in filters:
            wanted = cast("list[str]", filters["workflow_ids"])
            return [w for w in self.workflows if w.workflow_id in wanted]
        return self.workflows

    async def list_workflow_steps_async(
        self, workflow_id: str, **_: object
    ) -> list[StepInfo]:
        """Return the steps that the test prepared for one workflow."""
        return self.steps.get(workflow_id, [])

    async def list_queues_async(self) -> list[FakeQueue]:
        """Return the queues that the test prepared."""
        return self.queues

    async def list_queued_workflows_async(self, **_: object) -> list[WorkflowStatus]:
        """Return the workflows that wait on a queue or run from one."""
        return self.queued

    async def list_schedules_async(self) -> list[dict[str, object]]:
        """Return the schedules that the test prepared."""
        return self.schedules

    async def read_stream_async(
        self, workflow_id: str, key: str, *, offset: int = 0, **_: object
    ) -> AsyncGenerator[dict[str, object]]:
        """Replay the stream lines of one workflow, from an offset, then end."""
        self.calls.append({"stream": key, "workflow_id": workflow_id, "offset": offset})
        for line in self.streams.get(workflow_id, [])[offset:]:
            yield line

    async def check_connection_async(self) -> None:
        """Fail like the DBOS client does when the database is down."""
        if not self.connected:
            raise ConnectionError("database is down")


class FakeConnection:
    """A database connection that records the queries that it runs."""

    queries: list[str]

    def __init__(self, queries: list[str]) -> None:
        """Share the list of queries with the pool."""
        self.queries = queries

    async def execute(self, query: str) -> None:
        """Record the query."""
        self.queries.append(query)


class FakePool:
    """A connection pool that answers from memory."""

    reachable: bool
    queries: list[str]

    def __init__(self, *, reachable: bool = True) -> None:
        """Create a pool whose database is reachable or not."""
        self.reachable = reachable
        self.queries = []

    @asynccontextmanager
    async def connection(self, *, timeout: float) -> AsyncGenerator[FakeConnection]:
        """Hand out a connection, or time out like the real pool does."""
        if not self.reachable:
            raise PoolTimeout(f"pool is down after {timeout} s")
        yield FakeConnection(self.queries)


async def request_to(
    fake: FakeClient, path: str, pool: FakePool | None = None
) -> tuple[int, Any]:
    """Send one GET request to the app, with fakes in place of DBOS and the pool."""
    app.dependency_overrides[get_client] = lambda: fake
    app.dependency_overrides[get_pool] = lambda: pool or FakePool()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await http.get(path)
    finally:
        app.dependency_overrides.clear()
    return response.status_code, response.json()


async def events_from(
    fake: FakeClient, path: str, headers: dict[str, str] | None = None
) -> tuple[int, list[dict[str, str]]]:
    """Read a server-sent event stream and return its events as field maps."""
    app.dependency_overrides[get_client] = lambda: fake
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await http.get(path, headers=headers)
    finally:
        app.dependency_overrides.clear()

    events: list[dict[str, str]] = []
    for block in response.text.split("\n\n"):
        event: dict[str, str] = {}
        for line in block.splitlines():
            name, separator, value = line.partition(": ")
            if separator:
                event[name] = value
        if event:
            events.append(event)
    return response.status_code, events


def stream_reads(fake: FakeClient) -> list[dict[str, object]]:
    """Return the calls that read a log stream, in order."""
    return [call for call in fake.calls if "stream" in call]


def log_line(message: str, **fields: object) -> dict[str, object]:
    """Build one value of the log stream, as the engine writes it."""
    values: dict[str, object] = {
        "time": "2027-01-15T08:00:01.5Z",
        "level": "INFO",
        "message": message,
        "fields": {},
        "workflow_id": "run-1",
        "schema": None,
        "table": None,
    }
    return values | fields


def step(
    function_id: int,
    name: str,
    *,
    child_workflow_id: str | None = None,
    error: Exception | None = None,
) -> StepInfo:
    """Build the DBOS record of one finished step."""
    return {
        "function_id": function_id,
        "function_name": name,
        "output": None,
        "error": error,
        "child_workflow_id": child_workflow_id,
        "started_at_epoch_ms": NOW_MS + function_id * 1_000,
        "completed_at_epoch_ms": NOW_MS + function_id * 1_000 + 500,
    }


def child(workflow_id: str, name: str, **fields: object) -> WorkflowStatus:
    """Build a child workflow of the sync run."""
    return workflow(
        workflow_id=workflow_id,
        name=name,
        schedule_name=None,
        queue_name="dump",
        parent_workflow_id="run-1",
        **fields,
    )
