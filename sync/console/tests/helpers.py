import asyncio
from collections.abc import AsyncGenerator
from copy import copy
from dataclasses import dataclass, field
from typing import Any, cast

from dbos import StepInfo, WorkflowStatus
from dbos._error import DBOSException  # ruff: ignore[import-private-name]
from httpx2 import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.types import Message, Scope
from whenever import Instant

from console.dependencies import get_catalog, get_client, get_engine
from console.main import app

NOW_MS = 1_800_000_000_000


@dataclass
class ProxyAdmin:
    """A proxy admin server that remembers the paths that it was called with."""

    url: str
    calls: list[str] = field(default_factory=list)


def replace_status(source: WorkflowStatus, status: str) -> WorkflowStatus:
    """Return a copy of a workflow status that has another status."""
    changed = copy(source)
    vars(changed)["status"] = status
    return changed


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
class FakeHandle:
    """The handle of an enqueued workflow."""

    def get_workflow_id(self) -> str:
        """Return a fixed workflow id."""
        return "run-new"


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
    statuses: list[str] = field(default_factory=list)
    queued_timeline: list[list[WorkflowStatus]] = field(default_factory=list)
    polls: dict[str, int] = field(default_factory=dict)
    calls: list[dict[str, object]] = field(default_factory=list)
    enqueued: list[tuple[dict[str, object], tuple[object, ...]]] = field(
        default_factory=list
    )
    applied: list[dict[str, object]] = field(default_factory=list)

    def poll[T](self, kind: str, values: list[T]) -> T:
        """Return the value for the next poll of one kind. The last value repeats."""
        count = self.polls.get(kind, 0)
        self.polls[kind] = count + 1
        return values[min(count, len(values) - 1)]

    def by_id(self, wanted: list[str], *, polled: bool) -> list[WorkflowStatus]:
        """Find workflows by id. A polled run takes the next status of the timeline."""
        found = [w for w in self.workflows if w.workflow_id in wanted]
        if self.statuses and polled:
            status = self.poll("status", self.statuses)
            return [replace_status(w, status) for w in found]
        return found

    async def list_workflows_async(self, **filters: object) -> list[WorkflowStatus]:
        """Record the filters and answer like DBOS: by id, by parent, or all."""
        self.calls.append(filters)
        if (parent := filters.get("parent_workflow_id")) is not None:
            return self.children.get(str(parent), [])
        if "workflow_ids" in filters:
            wanted = cast("list[str]", filters["workflow_ids"])
            return self.by_id(wanted, polled=bool(filters.get("name")))
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
        if self.queued_timeline:
            return self.poll("queued", self.queued_timeline)
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

    async def enqueue_async(
        self, options: dict[str, object], *args: object
    ) -> FakeHandle:
        """Record the enqueue and return a handle with a fixed workflow id."""
        self.enqueued.append((options, args))
        return FakeHandle()

    async def retrieve_queue_async(self, name: str) -> FakeQueue | None:
        """Return one queue by name, or None."""
        return next((queue for queue in self.queues if queue.name == name), None)

    async def register_queue_async(  # ruff: ignore[too-many-arguments]
        self,
        name: str,
        *,
        global_concurrency: int | None = None,
        worker_concurrency: int | None = None,
        limiter: dict[str, float] | None = None,
        partition_concurrency: int | None = None,
        partition_worker_concurrency: int | None = None,
        partition_limiter: dict[str, float] | None = None,
        polling_interval_sec: float = 1.0,
        on_conflict: str = "always_update",
    ) -> FakeQueue:
        """Replace the whole queue, and refuse a worker limit above the global one, like DBOS."""
        assert on_conflict == "always_update"
        if (
            worker_concurrency is not None
            and global_concurrency is not None
            and worker_concurrency > global_concurrency
        ):
            raise ValueError(
                "concurrency must be greater than or equal to worker_concurrency"
            )
        queue = FakeQueue(
            name,
            global_concurrency,
            worker_concurrency,
            limiter,
            partition_concurrency,
            partition_worker_concurrency,
            partition_limiter,
            polling_interval_sec,
        )
        self.queues = [item for item in self.queues if item.name != name] + [queue]
        return queue

    async def apply_schedules_async(self, entries: list[dict[str, object]]) -> None:
        """Replace each schedule, which becomes active again, and refuse a cron without 5 or 6 fields."""
        for entry in entries:
            if len(str(entry["schedule"]).split()) not in {5, 6}:
                raise DBOSException(f"Invalid cron schedule: '{entry['schedule']}'")
            self.schedules = [
                item
                for item in self.schedules
                if item["schedule_name"] != entry["schedule_name"]
            ] + [
                schedule(
                    schedule_name=entry["schedule_name"],
                    workflow_name=entry["workflow_name"],
                    schedule=entry["schedule"],
                    cron_timezone=entry.get("cron_timezone"),
                    queue_name=entry.get("queue_name"),
                    automatic_backfill=entry.get("automatic_backfill", False),
                )
            ]
        self.applied.extend(entries)

    def pause_schedule(self, name: str) -> None:
        """Pause one schedule."""
        self.set_status(name, "PAUSED")

    def resume_schedule(self, name: str) -> None:
        """Resume one schedule."""
        self.set_status(name, "ACTIVE")

    def set_status(self, name: str, status: str) -> None:
        """Change the status of one schedule."""
        for item in self.schedules:
            if item["schedule_name"] == name:
                item["status"] = status

    async def check_connection_async(self) -> None:
        """Fail like the DBOS client does when the database is down."""
        if not self.connected:
            raise ConnectionError("database is down")


def unreachable_engine() -> AsyncEngine:
    """Return an engine for a database that nobody listens on. It connects only when used."""
    return create_async_engine("postgresql+psycopg://nobody@127.0.0.1:1/nobody")


async def send(  # ruff: ignore[too-many-arguments]
    method: str,
    path: str,
    fake: FakeClient | None = None,
    engine: AsyncEngine | None = None,
    *,
    headers: dict[str, str] | None = None,
    json: object | None = None,
) -> Response:
    """Send one request to the app, with a fake DBOS client and the given database engine."""
    app.dependency_overrides[get_client] = lambda: fake or FakeClient()
    app.dependency_overrides[get_engine] = lambda: engine or unreachable_engine()
    app.dependency_overrides[get_catalog] = lambda: None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            return await http.request(method, path, headers=headers, json=json)
    finally:
        app.dependency_overrides.clear()


async def request_to(
    fake: FakeClient, path: str, engine: AsyncEngine | None = None
) -> tuple[int, Any]:
    """Send one GET request and return the status and the JSON body."""
    response = await send("GET", path, fake, engine)
    return response.status_code, response.json()


def parse_events(text: str) -> list[dict[str, str]]:
    """Split the text of a server-sent event stream into complete events, as field maps."""
    blocks = text.split("\n\n")
    if not text.endswith("\n\n"):
        blocks = blocks[:-1]

    events: list[dict[str, str]] = []
    for block in blocks:
        event: dict[str, str] = {}
        for line in block.splitlines():
            name, separator, value = line.partition(": ")
            if separator and name:
                event[name] = value
        if event:
            events.append(event)
    return events


async def events_from(
    fake: FakeClient, path: str, headers: dict[str, str] | None = None
) -> tuple[int, list[dict[str, str]]]:
    """Read a server-sent event stream that ends, and return its events as field maps."""
    response = await send("GET", path, fake, headers=headers)
    return response.status_code, parse_events(response.text)


async def first_events(
    path: str, count: int, fake: FakeClient | None = None
) -> list[dict[str, str]]:
    """Read the first events of a stream that may never end, and then disconnect.

    A caller that already installed its own overrides passes no fake.
    """
    received = bytearray()
    enough = asyncio.Event()

    async def receive() -> Message:
        await enough.wait()
        return {"type": "http.disconnect"}

    async def send_message(message: Message) -> None:
        if message["type"] == "http.response.body":
            received.extend(message.get("body", b""))
            if len(parse_events(received.decode())) >= count:
                enough.set()

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("test", 80),
        "client": ("test", 1),
    }
    if fake is not None:
        app.dependency_overrides[get_client] = lambda: fake
        app.dependency_overrides[get_engine] = unreachable_engine
    try:
        await asyncio.wait_for(app(scope, receive, send_message), timeout=5)
    finally:
        if fake is not None:
            app.dependency_overrides.clear()
    return parse_events(received.decode())[:count]


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


def table(source: str, **fields: object) -> dict[str, object]:
    """Build one table of the config document, in the form that the API shows."""
    return {"source_table": source, "strategy": "full", **fields}


def document(
    tables: dict[str, dict[str, object]] | None = None, schema: str = "test"
) -> dict[str, object]:
    """Build a config document with one schema, in the form that the API shows."""
    return {
        "schemas": {
            schema: {
                "source": {"type": "bigquery"},
                "claim": "preferred_username",
                "ducklake": {"encrypted": False},
                "tables": tables or {"full_table": table("proj.ds.full_table")},
            }
        }
    }
