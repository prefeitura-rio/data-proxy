from helpers import FakeClient, at, request_to, schedule

FIRED_MS = 1_800_000_000_000


class TestSchedules:
    """The schedules of the sync application."""

    async def test_lists_the_schedules_with_their_cron_and_status(self) -> None:
        """Return each schedule with what it runs, when it runs and its state."""
        fake = FakeClient(schedules=[schedule()])

        status, body = await request_to(fake, "/schedules")

        assert status == 200
        assert body == [
            {
                "id": "schedule-1",
                "name": "sync",
                "workflow_name": "run_sync",
                "cron": "0 2 * * *",
                "status": "ACTIVE",
                "last_fired_at": None,
                "automatic_backfill": False,
                "timezone": None,
                "queue": "sync",
            }
        ]

    async def test_shows_when_a_schedule_fired_last_as_utc(self) -> None:
        """Write the last firing time in UTC, whatever offset DBOS stored."""
        fake = FakeClient(
            schedules=[schedule(last_fired_at="2027-01-15T05:00:00-03:00")]
        )

        _, body = await request_to(fake, "/schedules")

        assert body[0]["last_fired_at"] == at(FIRED_MS)

    async def test_shows_a_paused_schedule(self) -> None:
        """Report the status of a schedule that an operator paused."""
        fake = FakeClient(schedules=[schedule(status="PAUSED")])

        _, body = await request_to(fake, "/schedules")

        assert body[0]["status"] == "PAUSED"

    async def test_lists_nothing_when_there_is_no_schedule(self) -> None:
        """Return an empty list instead of an error."""
        assert await request_to(FakeClient(), "/schedules") == (200, [])
