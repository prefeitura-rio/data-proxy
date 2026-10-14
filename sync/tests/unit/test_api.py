from datetime import UTC, datetime
from typing import Any

import pytest
from dbos import WorkflowStatus
from httpx2 import ASGITransport, AsyncClient

from api.dependencies import get_client
from api.main import create_app

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
    """Return the ISO text that the API writes for a DBOS timestamp."""
    return (
        datetime.fromtimestamp(milliseconds / 1000, UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


class FakeClient:
    """A DBOS client that answers from memory."""

    connected: bool
    workflows: list[WorkflowStatus]
    calls: list[dict[str, object]]

    def __init__(
        self, *, connected: bool = True, workflows: list[WorkflowStatus] | None = None
    ) -> None:
        """Create a client that is connected or not, and knows some workflows."""
        self.connected = connected
        self.workflows = workflows or []
        self.calls = []

    async def list_workflows_async(self, **filters: object) -> list[WorkflowStatus]:
        """Record the filters and return the workflows that the test prepared."""
        self.calls.append(filters)
        return self.workflows

    async def check_connection_async(self) -> None:
        """Fail like the DBOS client does when the database is down."""
        if not self.connected:
            raise ConnectionError("database is down")


async def request_to(fake: FakeClient, path: str) -> tuple[int, Any]:
    """Send one GET request to an app that uses the fake client."""
    app = create_app()
    app.dependency_overrides[get_client] = lambda: fake
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get(path)
    return response.status_code, response.json()


class TestHealth:
    """Health and readiness behavior."""

    async def test_reports_that_the_process_is_alive(self) -> None:
        """Answer the liveness probe without touching the database."""
        assert await request_to(FakeClient(connected=False), "/health") == (
            200,
            {"status": "ok"},
        )

    async def test_reports_ready_when_the_database_answers(self) -> None:
        """Answer the readiness probe when the DBOS database is reachable."""
        assert await request_to(FakeClient(), "/ready") == (200, {"status": "ok"})

    async def test_reports_not_ready_when_the_database_is_down(self) -> None:
        """Answer 503 with the reason when the DBOS database is unreachable."""
        status, body = await request_to(FakeClient(connected=False), "/ready")

        assert status == 503
        assert body == {"detail": "database is down"}


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
        assert fake.calls[0]["start_time"] == "2026-10-01T00:00:00+00:00"

    @pytest.mark.parametrize(
        "query",
        ["status=BROKEN", "limit=0", "limit=201", "offset=-1", "since=yesterday"],
    )
    async def test_rejects_an_invalid_filter(self, query: str) -> None:
        """Answer 422 and do not ask DBOS for a filter that is not valid."""
        fake = FakeClient()

        status, _ = await request_to(fake, f"/runs?{query}")

        assert status == 422
        assert fake.calls == []
