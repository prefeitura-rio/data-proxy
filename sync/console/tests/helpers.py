from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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


class FakeClient:
    """A DBOS client that answers from memory."""

    connected: bool
    workflows: list[WorkflowStatus]
    children: dict[str, list[WorkflowStatus]]
    steps: dict[str, list[StepInfo]]
    calls: list[dict[str, object]]

    def __init__(
        self,
        *,
        connected: bool = True,
        workflows: list[WorkflowStatus] | None = None,
        children: dict[str, list[WorkflowStatus]] | None = None,
        steps: dict[str, list[StepInfo]] | None = None,
    ) -> None:
        """Create a client that is connected or not, and knows some workflows."""
        self.connected = connected
        self.workflows = workflows or []
        self.children = children or {}
        self.steps = steps or {}
        self.calls = []

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
