import pytest

from sync.schedules import (
    SchedulePlan,
    is_cron,
    plan_schedules,
    schedule_name,
)


class TestScheduleName:
    """A schedule is named after its schema."""

    def test_the_name_has_the_prefix(self) -> None:
        """Keep the prefix that the console views and functions read."""
        assert schedule_name("hr") == "sync-hr"


class TestPlanSchedules:
    """The plan makes the schedules of DBOS match the schemas of the console."""

    @pytest.mark.parametrize(
        ("schemas", "existing", "expected"),
        [
            pytest.param(
                {"hr", "sales"},
                set[str](),
                SchedulePlan(create=["hr", "sales"], delete=[]),
                id="a-new-deployment-gets-one-schedule-for-each-schema",
            ),
            pytest.param(
                {"hr", "sales"},
                {"sync-hr", "sync-sales"},
                SchedulePlan(create=[], delete=[]),
                id="nothing-to-do",
            ),
            pytest.param(
                {"hr", "sales"},
                {"sync-hr"},
                SchedulePlan(create=["sales"], delete=[]),
                id="a-new-schema-gets-a-schedule",
            ),
            pytest.param(
                {"hr"},
                {"sync-hr", "sync-gone"},
                SchedulePlan(create=[], delete=["sync-gone"]),
                id="a-removed-schema-loses-its-schedule",
            ),
            pytest.param(
                {"hr"},
                {"sync", "sync-hr"},
                SchedulePlan(create=[], delete=["sync"]),
                id="the-old-global-schedule-goes",
            ),
            pytest.param(
                {"hr"},
                {"nightly", "sync-hr"},
                SchedulePlan(create=[], delete=[]),
                id="a-schedule-of-no-schema-stays",
            ),
            pytest.param(
                {"hr"},
                {"nightly", "sync-hr", "sync-gone"},
                SchedulePlan(create=[], delete=["sync-gone"]),
                id="a-schedule-of-no-schema-stays-beside-a-removed-one",
            ),
            pytest.param(
                {"b", "a"},
                {"sync-z", "sync-y"},
                SchedulePlan(create=["a", "b"], delete=["sync-y", "sync-z"]),
                id="the-lists-are-sorted",
            ),
            pytest.param(
                set[str](),
                {"sync", "sync-hr"},
                SchedulePlan(create=[], delete=["sync", "sync-hr"]),
                id="no-schema-means-no-schedule",
            ),
        ],
    )
    def test_creates_and_deletes_what_differs(
        self, schemas: set[str], existing: set[str], expected: SchedulePlan
    ) -> None:
        """Return the schemas to create and the schedules to delete."""
        assert plan_schedules(schemas, existing) == expected


class TestIsCron:
    """The check reads a cron expression as DBOS does: 5 fields, or 6 with the seconds first."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("0 2 * * *", True, id="five-fields"),
            pytest.param("*/30 0 2 * * *", True, id="six-fields-with-seconds"),
            pytest.param("0 2 * * mon", True, id="a-day-name"),
            pytest.param("0 2 * *", False, id="four-fields"),
            pytest.param("61 2 * * *", False, id="a-minute-out-of-range"),
            pytest.param("0 2 * * ;", False, id="a-bad-character"),
            pytest.param("", False, id="empty"),
        ],
    )
    def test_accepts_only_what_dbos_accepts(self, text: str, expected: bool) -> None:
        """Say if the text is a cron expression."""
        assert is_cron(text) is expected
