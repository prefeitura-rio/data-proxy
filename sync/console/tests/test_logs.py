import json

import pytest

from helpers import FakeClient, events_from, log_line, stream_reads, workflow


class TestLogs:
    """The log stream of one workflow."""

    async def test_streams_each_line_as_an_event_and_then_the_end(self) -> None:
        """Send every line with its offset as the id, then the final status."""
        fake = FakeClient(
            workflows=[workflow(workflow_id="run-1", status="SUCCESS")],
            streams={
                "run-1": [
                    log_line("Sync workflow started"),
                    log_line(
                        "Dump completed",
                        level="ERROR",
                        fields={"task_id": "t-1", "failed_paths": 2},
                        schema="test",
                        table="full_table",
                    ),
                ]
            },
        )

        status, events = await events_from(fake, "/workflows/run-1/logs")

        assert status == 200
        assert [(e["event"], e.get("id")) for e in events] == [
            ("log", "0"),
            ("log", "1"),
            ("end", None),
        ]
        assert json.loads(events[1]["data"]) == {
            "time": "2027-01-15T08:00:01.5Z",
            "level": "ERROR",
            "message": "Dump completed",
            "fields": {"task_id": "t-1", "failed_paths": 2},
            "workflow_id": "run-1",
            "schema_name": "test",
            "table_name": "full_table",
        }
        assert json.loads(events[2]["data"]) == {
            "workflow_id": "run-1",
            "status": "SUCCESS",
        }

    async def test_reads_the_stream_from_the_offset_in_the_query(self) -> None:
        """Skip the lines before the offset and keep the ids in step."""
        fake = FakeClient(
            workflows=[workflow(workflow_id="run-1")],
            streams={"run-1": [log_line("a"), log_line("b"), log_line("c")]},
        )

        _, events = await events_from(fake, "/workflows/run-1/logs?offset=2")

        assert [e.get("id") for e in events] == ["2", None]
        assert stream_reads(fake)[0]["offset"] == 2

    async def test_resumes_after_the_last_event_id_of_a_reconnecting_browser(
        self,
    ) -> None:
        """Continue with the line after the one that the browser saw last."""
        fake = FakeClient(
            workflows=[workflow(workflow_id="run-1")],
            streams={"run-1": [log_line(str(n)) for n in range(6)]},
        )

        _, events = await events_from(
            fake, "/workflows/run-1/logs", headers={"Last-Event-ID": "3"}
        )

        assert [e.get("id") for e in events] == ["4", "5", None]
        assert stream_reads(fake)[0]["offset"] == 4

    async def test_ends_at_once_for_a_workflow_without_log_lines(self) -> None:
        """Send only the end event when the workflow never wrote a line."""
        fake = FakeClient(workflows=[workflow(workflow_id="run-1", status="ERROR")])

        _, events = await events_from(fake, "/workflows/run-1/logs")

        assert [e["event"] for e in events] == ["end"]
        assert json.loads(events[0]["data"])["status"] == "ERROR"

    async def test_answers_404_for_an_unknown_workflow(self) -> None:
        """Refuse before any streaming starts."""
        fake = FakeClient()

        status, _ = await events_from(fake, "/workflows/missing/logs")

        assert status == 404
        assert stream_reads(fake) == []

    @pytest.mark.parametrize("query", ["offset=-1", "offset=abc"])
    async def test_rejects_an_invalid_offset(self, query: str) -> None:
        """Answer 422 and do not read the stream."""
        fake = FakeClient(workflows=[workflow(workflow_id="run-1")])

        status, _ = await events_from(fake, f"/workflows/run-1/logs?{query}")

        assert status == 422
        assert stream_reads(fake) == []
