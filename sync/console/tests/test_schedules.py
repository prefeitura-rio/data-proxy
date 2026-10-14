import pytest

from helpers import FakeClient, at, request_to, schedule, send

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


class TestChangeSchedule:
    """Change when a schedule fires, or pause it."""

    async def test_changes_the_cron_and_keeps_the_rest(self) -> None:
        """Replace the schedule with the new cron, on the same workflow and queue."""
        fake = FakeClient(schedules=[schedule()])

        response = await send(
            "PATCH", "/schedules/sync", fake, json={"cron": "0 3 * * *"}
        )

        assert response.status_code == 200
        assert response.json() | {"id": None} == {
            "id": None,
            "name": "sync",
            "workflow_name": "run_sync",
            "cron": "0 3 * * *",
            "status": "ACTIVE",
            "last_fired_at": None,
            "automatic_backfill": False,
            "timezone": None,
            "queue": "sync",
        }

    @pytest.mark.parametrize(
        ("stored", "patch", "expected"),
        [
            pytest.param("ACTIVE", {"status": "PAUSED"}, "PAUSED", id="pause"),
            pytest.param("PAUSED", {"status": "ACTIVE"}, "ACTIVE", id="resume"),
            pytest.param(
                "PAUSED", {"cron": "0 3 * * *"}, "PAUSED", id="cron-keeps-it-paused"
            ),
            pytest.param(
                "PAUSED",
                {"cron": "0 3 * * *", "status": "ACTIVE"},
                "ACTIVE",
                id="cron-and-resume",
            ),
        ],
    )
    async def test_pauses_and_resumes(
        self, stored: str, patch: dict[str, object], expected: str
    ) -> None:
        """End in the status that the patch asks for, or keep the stored one."""
        fake = FakeClient(schedules=[schedule(status=stored)])

        response = await send("PATCH", "/schedules/sync", fake, json=patch)

        assert response.json()["status"] == expected

    async def test_changes_the_time_zone_and_clears_it_with_null(self) -> None:
        """Set a zone, and remove it again."""
        fake = FakeClient(schedules=[schedule()])

        zone = await send(
            "PATCH", "/schedules/sync", fake, json={"timezone": "America/Sao_Paulo"}
        )
        cleared = await send("PATCH", "/schedules/sync", fake, json={"timezone": None})

        assert zone.json()["timezone"] == "America/Sao_Paulo"
        assert cleared.json()["timezone"] is None

    async def test_only_a_pause_leaves_the_schedule_in_place(self) -> None:
        """Don't replace the schedule when nothing but the status changes."""
        fake = FakeClient(schedules=[schedule()])

        await send("PATCH", "/schedules/sync", fake, json={"status": "PAUSED"})

        assert fake.applied == []

    async def test_answers_404_for_an_unknown_schedule(self) -> None:
        """Change nothing, and say so."""
        response = await send(
            "PATCH", "/schedules/missing", FakeClient(), json={"cron": "0 3 * * *"}
        )

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_refuses_a_cron_that_does_not_parse(self) -> None:
        """Answer 422 with a pointer to the cron, and keep the stored schedule."""
        fake = FakeClient(schedules=[schedule()])

        response = await send("PATCH", "/schedules/sync", fake, json={"cron": "nope"})

        assert response.status_code == 422
        assert [item["pointer"] for item in response.json()["errors"]] == ["/cron"]
        assert fake.schedules[0]["schedule"] == "0 2 * * *"
