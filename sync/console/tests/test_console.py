import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from dbos import StepInfo, WorkflowStatus
from fastapi import APIRouter
from fastapi.routing import iter_route_contexts
from httpx2 import ASGITransport, AsyncClient
from psycopg_pool import PoolTimeout
from whenever import Instant

from console import routes
from console.dependencies import get_client, get_pool
from console.main import app
from console.registry import discover_routers

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


class TestHealth:
    """Health and readiness behavior."""

    async def test_reports_that_the_process_is_alive(self) -> None:
        """Answer the liveness probe without touching the database."""
        pool = FakePool(reachable=False)

        assert await request_to(FakeClient(connected=False), "/health", pool) == (
            200,
            {"status": "ok"},
        )
        assert pool.queries == []

    async def test_reports_ready_when_both_databases_answer(self) -> None:
        """Answer the readiness probe when DBOS and the app database are reachable."""
        pool = FakePool()

        assert await request_to(FakeClient(), "/ready", pool) == (
            200,
            {"status": "ok"},
        )
        assert pool.queries == ["SELECT 1"]

    async def test_reports_not_ready_when_the_app_database_is_down(self) -> None:
        """Answer 503 with the same generic reason when only the pool fails."""
        status, body = await request_to(
            FakeClient(), "/ready", FakePool(reachable=False)
        )

        assert status == 503
        assert body == {"detail": "database unavailable"}

    async def test_logs_the_reason_that_the_app_database_is_down(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Keep the pool error in the log, where the operator can read it."""
        with caplog.at_level("ERROR"):
            await request_to(FakeClient(), "/ready", FakePool(reachable=False))

        assert "pool is down" in caplog.text

    async def test_reports_not_ready_when_the_database_is_down(self) -> None:
        """Answer 503 with a generic reason, without the details of the failure."""
        status, body = await request_to(FakeClient(connected=False), "/ready")

        assert status == 503
        assert body == {"detail": "database unavailable"}

    async def test_logs_the_reason_that_the_database_is_down(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Keep the real error in the log, where the operator can read it."""
        with caplog.at_level("ERROR"):
            await request_to(FakeClient(connected=False), "/ready")

        assert "database is down" in caplog.text


class TestRuns:
    """The list of sync runs."""

    async def test_lists_the_runs_newest_first_with_their_timing(self) -> None:
        """Map one DBOS workflow to a run with its start, end and duration."""
        fake = FakeClient(workflows=[workflow()])

        status, body = await request_to(fake, "/runs")

        assert status == 200
        assert body == {
            "items": [
                {
                    "id": "run-1",
                    "status": "SUCCESS",
                    "trigger": "schedule",
                    "schedule_name": "sync-schedule",
                    "queue": "sync",
                    "created_at": at(NOW_MS),
                    "started_at": at(NOW_MS + 1_000),
                    "finished_at": at(NOW_MS + 61_000),
                    "duration_seconds": 60.0,
                    "error": None,
                }
            ],
            "limit": 50,
            "offset": 0,
            "has_more": False,
        }
        assert fake.calls == [
            {
                "name": "run_sync",
                "status": None,
                "start_time": None,
                "limit": 51,
                "offset": 0,
                "sort_desc": True,
                "load_input": False,
                "load_output": False,
            }
        ]

    async def test_marks_a_run_without_a_schedule_as_manual(self) -> None:
        """Tell a run that a person or a webhook started from a scheduled one."""
        fake = FakeClient(workflows=[workflow(schedule_name=None)])

        _, body = await request_to(fake, "/runs")

        assert body["items"][0]["trigger"] == "manual"
        assert body["items"][0]["schedule_name"] is None

    async def test_gives_no_duration_for_a_run_that_has_not_finished(self) -> None:
        """Leave the end and the duration empty while a run still works."""
        fake = FakeClient(workflows=[workflow(status="PENDING", completed_at=None)])

        _, body = await request_to(fake, "/runs")

        assert body["items"][0]["status"] == "PENDING"
        assert body["items"][0]["finished_at"] is None
        assert body["items"][0]["duration_seconds"] is None

    async def test_reports_the_error_of_a_failed_run(self) -> None:
        """Show the type and the message of the error that failed the run."""
        fake = FakeClient(
            workflows=[workflow(status="ERROR", error=RuntimeError("publish failed"))]
        )

        _, body = await request_to(fake, "/runs")

        assert body["items"][0]["error"] == "RuntimeError: publish failed"

    async def test_pages_with_one_extra_row_to_know_if_more_exist(self) -> None:
        """Ask for one row more than the page and cut it from the answer."""
        fake = FakeClient(
            workflows=[workflow(workflow_id=f"run-{n}") for n in range(3)]
        )

        _, body = await request_to(fake, "/runs?limit=2&offset=4")

        assert [run["id"] for run in body["items"]] == ["run-0", "run-1"]
        assert (body["limit"], body["offset"], body["has_more"]) == (2, 4, True)
        assert fake.calls[0]["limit"] == 3
        assert fake.calls[0]["offset"] == 4

    async def test_filters_by_status_and_start_time(self) -> None:
        """Pass the status and the earliest start to DBOS."""
        fake = FakeClient()

        await request_to(fake, "/runs?status=ERROR&since=2026-10-01T00:00:00Z")

        assert fake.calls[0]["status"] == "ERROR"
        assert fake.calls[0]["start_time"] == "2026-10-01T00:00:00Z"

    async def test_normalizes_the_start_time_to_utc(self) -> None:
        """Convert a start time with an offset to the same instant in UTC."""
        fake = FakeClient()

        await request_to(fake, "/runs?since=2026-10-01T03:00:00%2B03:00")

        assert fake.calls[0]["start_time"] == "2026-10-01T00:00:00Z"

    async def test_documents_the_start_time_as_a_date_time(self) -> None:
        """Keep the OpenAPI schema of the start time a date-time string."""
        _, body = await request_to(FakeClient(), "/openapi.json")

        parameters: list[dict[str, Any]] = body["paths"]["/runs"]["get"]["parameters"]
        since: dict[str, Any] = next(
            item for item in parameters if item["name"] == "since"
        )
        assert {"type": "string", "format": "date-time"} in since["schema"]["anyOf"]

    @pytest.mark.parametrize(
        "query",
        [
            "status=BROKEN",
            "limit=0",
            "limit=201",
            "offset=-1",
            "since=yesterday",
            "since=2026-10-01T00:00:00",
        ],
    )
    async def test_rejects_an_invalid_filter(self, query: str) -> None:
        """Answer 422 and do not ask DBOS for a filter that is not valid."""
        fake = FakeClient()

        status, _ = await request_to(fake, f"/runs?{query}")

        assert status == 422
        assert fake.calls == []


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


class TestRun:
    """One sync run with its steps and child workflows."""

    async def test_shows_the_run_with_its_steps_and_children(self) -> None:
        """Return the run, its ordered steps, its children and their counts."""
        fake = FakeClient(
            workflows=[workflow()],
            steps={
                "run-1": [
                    step(1, "build_sync_work"),
                    step(
                        2,
                        "dump_task",
                        child_workflow_id="dump-1",
                        error=ValueError("bad row"),
                    ),
                ]
            },
            children={
                "run-1": [
                    child("dump-1", "dump_task"),
                    child("dump-2", "dump_task", status="ERROR", error=OSError("S3")),
                    child("publish-1", "publish_schema"),
                ]
            },
        )

        status, body = await request_to(fake, "/runs/run-1")

        assert status == 200
        assert body["id"] == "run-1"
        assert body["status"] == "SUCCESS"
        assert body["duration_seconds"] == pytest.approx(60.0)
        assert body["steps"] == [
            {
                "id": 1,
                "name": "build_sync_work",
                "started_at": at(NOW_MS + 1_000),
                "finished_at": at(NOW_MS + 1_500),
                "duration_seconds": 0.5,
                "child_workflow_id": None,
                "error": None,
            },
            {
                "id": 2,
                "name": "dump_task",
                "started_at": at(NOW_MS + 2_000),
                "finished_at": at(NOW_MS + 2_500),
                "duration_seconds": 0.5,
                "child_workflow_id": "dump-1",
                "error": "ValueError: bad row",
            },
        ]
        assert [(c["id"], c["name"], c["status"]) for c in body["children"]] == [
            ("dump-1", "dump_task", "SUCCESS"),
            ("dump-2", "dump_task", "ERROR"),
            ("publish-1", "publish_schema", "SUCCESS"),
        ]
        assert body["children"][1]["error"] == "OSError: S3"
        assert body["child_counts"] == {
            "dump_task": {"SUCCESS": 1, "ERROR": 1},
            "publish_schema": {"SUCCESS": 1},
        }

    async def test_looks_up_only_the_sync_workflow_and_its_children(self) -> None:
        """Ask DBOS for the run by id and name, and for the children by parent."""
        fake = FakeClient(workflows=[workflow()])

        await request_to(fake, "/runs/run-1")

        assert fake.calls[0] == {
            "name": "run_sync",
            "workflow_ids": ["run-1"],
            "load_input": False,
            "load_output": False,
        }
        assert fake.calls[1] == {
            "parent_workflow_id": "run-1",
            "load_input": False,
            "load_output": False,
        }

    async def test_answers_404_for_a_run_that_does_not_exist(self) -> None:
        """Answer 404 when DBOS knows no sync run with that id."""
        status, body = await request_to(FakeClient(), "/runs/missing")

        assert status == 404
        assert body == {"detail": "run not found"}


def paths_of(routers: list[APIRouter]) -> list[str]:
    """List the effective paths that the routers declare, in order."""
    return [
        path
        for router in routers
        for context in iter_route_contexts(router.routes)
        if (path := context.path) is not None
    ]


def make_package(
    root: Path, monkeypatch: pytest.MonkeyPatch, name: str, modules: dict[str, str]
) -> ModuleType:
    """Write a package of modules under root, make it importable, and import it."""
    package = root / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    for module, source in modules.items():
        (package / f"{module}.py").write_text(source)
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])
    return import_module(name)


def route_module(path: str) -> str:
    """Return the source of a route module with one route at the path."""
    return "\n".join(
        [
            "from fastapi import APIRouter",
            "router = APIRouter()",
            f"router.add_api_route('{path}', lambda: None)",
        ]
    )


class TestRoutes:
    """The registration of the route modules."""

    def test_finds_the_router_of_every_route_module(self) -> None:
        """Collect the routes of each public module in the routes package."""
        assert set(paths_of(discover_routers(routes))) == {
            "/health",
            "/ready",
            "/runs",
            "/runs/{run_id}",
        }

    def test_serves_everything_that_the_route_modules_declare(self) -> None:
        """Include every discovered router in the application."""
        assert set(app.openapi()["paths"]) == {
            "/health",
            "/ready",
            "/runs",
            "/runs/{run_id}",
        }

    def test_registers_modules_in_name_order_and_skips_private_ones(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Import public modules by name, and leave the private ones alone."""
        package = make_package(
            tmp_path,
            monkeypatch,
            "fake_routes",
            {
                "b_second": route_module("/b"),
                "a_first": route_module("/a"),
                "_helper": "raise RuntimeError('never imported')",
            },
        )

        assert paths_of(discover_routers(package)) == ["/a", "/b"]

    def test_fails_for_a_public_module_without_a_router(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise instead of skipping a route module that declares no router."""
        package = make_package(
            tmp_path, monkeypatch, "broken_routes", {"forgotten": "value = 1"}
        )

        with pytest.raises(
            TypeError, match=r"broken_routes\.forgotten must declare a router"
        ):
            discover_routers(package)
