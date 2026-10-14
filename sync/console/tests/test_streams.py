import json

from helpers import (
    FakeClient,
    FakeQueue,
    child,
    events_from,
    first_events,
    step,
    workflow,
)


class TestQueuesStream:
    """The queues, streamed as a metric."""

    async def test_sends_the_queues_now_and_again_when_a_depth_changes(self) -> None:
        """Send one event for the first poll and one for each change, and none for a repeat."""
        waiting = workflow(workflow_id="d1", queue_name="dump", status="ENQUEUED")
        fake = FakeClient(
            queues=[FakeQueue("dump")], queued_timeline=[[], [], [waiting]]
        )

        events = await first_events("/queues/stream", 2, fake)

        assert [event["event"] for event in events] == ["queues", "queues"]
        assert [json.loads(event["data"])[0]["depth"] for event in events] == [
            {},
            {"ENQUEUED": 1},
        ]


class TestRunStream:
    """The phases of one run, streamed while it runs."""

    async def test_sends_the_phases_of_a_finished_run_and_then_the_end(self) -> None:
        """Send the status, the visible steps and the children by status, then end."""
        fake = FakeClient(
            workflows=[workflow(status="SUCCESS")],
            steps={
                "run-1": [
                    step(1, "DBOS.writeStream"),
                    step(2, "build_sync_work"),
                    step(3, "dump_task", child_workflow_id="dump-1"),
                ]
            },
            children={
                "run-1": [
                    child("dump-1", "dump_task"),
                    child("dump-2", "dump_task", status="ERROR"),
                ]
            },
        )

        events = await first_events("/runs/run-1/stream", 10, fake)

        assert [event["event"] for event in events] == ["run", "end"]
        progress = json.loads(events[0]["data"])
        assert progress["status"] == "SUCCESS"
        assert [item["name"] for item in progress["steps"]] == [
            "build_sync_work",
            "dump_task",
        ]
        assert progress["child_counts"] == {"dump_task": {"SUCCESS": 1, "ERROR": 1}}
        assert json.loads(events[1]["data"]) == {
            "workflow_id": "run-1",
            "status": "SUCCESS",
        }

    async def test_sends_a_new_event_only_when_the_run_changes(self) -> None:
        """Skip a poll that shows nothing new, and end when the run finishes."""
        fake = FakeClient(
            workflows=[workflow()], statuses=["PENDING", "PENDING", "SUCCESS"]
        )

        events = await first_events("/runs/run-1/stream", 10, fake)

        assert [event["event"] for event in events] == ["run", "run", "end"]
        assert [json.loads(event["data"])["status"] for event in events[:2]] == [
            "PENDING",
            "SUCCESS",
        ]

    async def test_answers_404_for_an_unknown_run(self) -> None:
        """Refuse before any streaming starts."""
        status, _ = await events_from(FakeClient(), "/runs/missing/stream")

        assert status == 404
