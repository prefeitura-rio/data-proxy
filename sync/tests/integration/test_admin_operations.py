import psycopg
import pytest
from psycopg.errors import CheckViolation, InsufficientPrivilege

from tests.helpers import become, rows

QUEUE = "SELECT concurrency, worker_concurrency, rate_limit_max, polling_interval_sec FROM admin.queues WHERE name = 'q-ops'"
SCHEDULE = "SELECT schedule, status, timezone FROM admin.schedules WHERE name = 's-ops'"


@pytest.fixture
async def ops(admin_db: psycopg.AsyncConnection) -> psycopg.AsyncConnection:
    """Add one queue and one schedule that the operator edits."""
    await admin_db.execute(
        b"""
        INSERT INTO dbos.queues (name, concurrency, worker_concurrency, rate_limit_max, polling_interval_sec)
        VALUES ('q-ops', 4, 2, 10, 1.0)
        """
    )
    await admin_db.execute(
        b"""
        INSERT INTO dbos.workflow_schedules (schedule_id, schedule_name, workflow_name, schedule, status, context, cron_timezone)
        VALUES ('id-ops', 's-ops', 'run_sync', '0 3 * * *', 'ACTIVE', '', 'UTC')
        """
    )
    return admin_db


@pytest.mark.postgres
class TestQueueSettings:
    """Operators change the limits of a queue with a PATCH, and bad values are refused."""

    @pytest.mark.parametrize("role", ["console_admin"])
    async def test_a_patch_changes_only_the_given_settings(
        self, ops: psycopg.AsyncConnection, role: str
    ) -> None:
        """The columns that the update doesn't name keep their value."""
        await become(ops, role)

        await ops.execute(
            b"UPDATE admin.queues SET concurrency = 8, polling_interval_sec = 0.5 WHERE name = 'q-ops'"
        )

        assert await rows(ops, QUEUE) == [(8, 2, 10, 0.5)]

    async def test_the_update_is_stamped_for_the_worker(
        self, ops: psycopg.AsyncConnection
    ) -> None:
        """The change moves updated_at forward."""
        await ops.execute(b"UPDATE dbos.queues SET updated_at = 0 WHERE name = 'q-ops'")
        await become(ops, "console_admin")

        await ops.execute(
            b"UPDATE admin.queues SET concurrency = 3 WHERE name = 'q-ops'"
        )

        stamped = await rows(
            ops,
            "SELECT updated_at > now() - interval '1 minute' FROM admin.queues WHERE name = 'q-ops'",
        )
        assert stamped == [(True,)]

    @pytest.mark.parametrize(
        "change",
        [
            pytest.param("concurrency = 0", id="zero-concurrency"),
            pytest.param("worker_concurrency = 9", id="worker-above-total"),
            pytest.param("rate_limit_max = -1", id="negative-rate-limit"),
            pytest.param("polling_interval_sec = 0", id="zero-polling"),
        ],
    )
    async def test_invalid_settings_are_refused(
        self, ops: psycopg.AsyncConnection, change: str
    ) -> None:
        """A value that DBOS would reject never reaches the queue."""
        await become(ops, "console_admin")

        with pytest.raises(CheckViolation):
            await ops.execute(
                f"UPDATE admin.queues SET {change} WHERE name = 'q-ops'".encode()
            )

    @pytest.mark.parametrize(
        "role", ["console_viewer", "console_operator", "console_editor"]
    )
    async def test_lower_levels_cannot_change_a_queue(
        self, ops: psycopg.AsyncConnection, role: str
    ) -> None:
        """Only operators and admins can edit queues."""
        await become(ops, role)

        with pytest.raises(InsufficientPrivilege):
            await ops.execute(b"UPDATE admin.queues SET concurrency = 8")

    async def test_the_name_of_a_queue_is_fixed(
        self, ops: psycopg.AsyncConnection
    ) -> None:
        """An operator can't rename a queue."""
        await become(ops, "console_admin")

        with pytest.raises(InsufficientPrivilege):
            await ops.execute(b"UPDATE admin.queues SET name = 'other'")


@pytest.mark.postgres
class TestScheduleSettings:
    """Operators pause a schedule and change its cron expression, and bad values are refused."""

    async def test_an_operator_pauses_and_retimes_a_schedule(
        self, ops: psycopg.AsyncConnection
    ) -> None:
        """Status, cron expression, and time zone change together."""
        await become(ops, "console_admin")

        await ops.execute(
            b"UPDATE admin.schedules SET status = 'PAUSED', schedule = '*/5 * * * *', timezone = 'America/Sao_Paulo' WHERE name = 's-ops'"
        )

        assert await rows(ops, SCHEDULE) == [
            ("*/5 * * * *", "PAUSED", "America/Sao_Paulo")
        ]

    @pytest.mark.parametrize(
        "schedule",
        ["0 3 * * *", "*/15 1-5 * * mon-fri", "30 0 3 * * *", "0 0,12 1 jan *"],
    )
    async def test_cron_expressions_in_use_are_accepted(
        self, ops: psycopg.AsyncConnection, schedule: str
    ) -> None:
        """Five and six field expressions with lists, ranges, steps, and names pass."""
        await become(ops, "console_admin")

        await ops.execute(
            b"UPDATE admin.schedules SET schedule = %s WHERE name = 's-ops'",
            (schedule,),
        )

        assert await rows(ops, SCHEDULE) == [(schedule, "ACTIVE", "UTC")]

    @pytest.mark.parametrize(
        "change",
        [
            pytest.param("schedule = 'daily'", id="not-cron"),
            pytest.param("schedule = '* *'", id="too-few-fields"),
            pytest.param("schedule = '* * * * * * *'", id="too-many-fields"),
            pytest.param("schedule = '0 3 * * *; DROP'", id="stray-characters"),
            pytest.param("status = 'DELETED'", id="unknown-status"),
            pytest.param("timezone = 'Mars/Base'", id="unknown-timezone"),
        ],
    )
    async def test_invalid_values_are_refused(
        self, ops: psycopg.AsyncConnection, change: str
    ) -> None:
        """A value that could stop the scheduler never reaches the table."""
        await become(ops, "console_admin")

        with pytest.raises(CheckViolation):
            await ops.execute(
                f"UPDATE admin.schedules SET {change} WHERE name = 's-ops'".encode()
            )

    @pytest.mark.parametrize(
        "role", ["console_viewer", "console_operator", "console_editor"]
    )
    async def test_lower_levels_cannot_change_a_schedule(
        self, ops: psycopg.AsyncConnection, role: str
    ) -> None:
        """Only operators and admins can edit schedules."""
        await become(ops, role)

        with pytest.raises(InsufficientPrivilege):
            await ops.execute(b"UPDATE admin.schedules SET status = 'PAUSED'")

    async def test_the_workflow_of_a_schedule_is_fixed(
        self, ops: psycopg.AsyncConnection
    ) -> None:
        """An operator can't point a schedule at another workflow."""
        await become(ops, "console_admin")

        with pytest.raises(InsufficientPrivilege):
            await ops.execute(b"UPDATE admin.schedules SET workflow_name = 'other'")
