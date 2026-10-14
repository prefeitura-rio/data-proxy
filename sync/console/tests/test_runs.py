from typing import Any

import pytest

from helpers import NOW_MS, FakeClient, at, child, request_to, step, workflow


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


class TestRun:
    """One sync run with its steps and child workflows."""

    async def test_hides_the_internal_steps_of_dbos_by_default(self) -> None:
        """Leave out the steps that DBOS records for its own operations."""
        fake = FakeClient(
            workflows=[workflow()],
            steps={
                "run-1": [
                    step(1, "DBOS.writeStream"),
                    step(2, "build_sync_work"),
                    step(3, "DBOS.writeStream"),
                ]
            },
        )

        _, body = await request_to(fake, "/runs/run-1")

        assert [item["name"] for item in body["steps"]] == ["build_sync_work"]

    async def test_shows_the_internal_steps_when_asked(self) -> None:
        """Keep every step when the caller asks for the internal ones."""
        fake = FakeClient(
            workflows=[workflow()],
            steps={"run-1": [step(1, "DBOS.writeStream"), step(2, "build_sync_work")]},
        )

        _, body = await request_to(fake, "/runs/run-1?include_internal=true")

        assert [item["name"] for item in body["steps"]] == [
            "DBOS.writeStream",
            "build_sync_work",
        ]

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
