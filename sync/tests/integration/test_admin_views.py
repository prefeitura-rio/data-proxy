from datetime import UTC, datetime

import psycopg
import pytest

from tests.helpers import rows

START_MS = 1_700_000_000_000
START = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
END = datetime(2023, 11, 14, 22, 13, 50, tzinfo=UTC)
END_MS = START_MS + 30_000

WORKFLOW = """
INSERT INTO dbos.workflow_status (
    workflow_uuid, status, name, queue_name, queue_partition_key, schedule_name,
    authenticated_user, parent_workflow_id, created_at, started_at_epoch_ms, completed_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


async def workflow(
    connection: psycopg.AsyncConnection,
    workflow_id: str,
    name: str,
    *,
    status: str = "SUCCESS",
    queue: str | None = None,
    partition: str | None = None,
    schedule: str | None = None,
    user: str | None = None,
    parent: str | None = None,
) -> None:
    """Insert one DBOS workflow row that started at START and ended at END."""
    await connection.execute(
        WORKFLOW.encode(),
        (
            workflow_id,
            status,
            name,
            queue,
            partition,
            schedule,
            user,
            parent,
            START_MS,
            START_MS,
            END_MS,
        ),
    )


@pytest.mark.postgres
class TestRuns:
    """The runs views list sync runs and what they did."""

    async def test_runs_lists_only_sync_runs_with_readable_times(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Only run_sync workflows are runs, with readable times and a count of their tasks."""
        await workflow(
            admin_db,
            "run-1",
            "run_sync",
            queue="sync",
            schedule="sync",
            user="ana",
        )
        await workflow(admin_db, "task-1", "dump_task", parent="run-1")
        await workflow(
            admin_db, "task-2", "publish_schema", parent="run-1", status="ERROR"
        )

        result = await rows(
            admin_db,
            "SELECT id, status, queue_name, schedule_name, started_by, created_at, started_at, completed_at, task_count, failed_task_count FROM admin.runs WHERE id LIKE 'run-%' OR id LIKE 'task-%'",
        )

        assert result == [
            ("run-1", "SUCCESS", "sync", "sync", "ana", START, START, END, 2, 1)
        ]

    async def test_run_tasks_lists_the_children_of_a_run(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Dump and publish workflows are the tasks of the run that started them."""
        await workflow(admin_db, "run-1", "run_sync")
        await workflow(admin_db, "dump-1", "dump_task", parent="run-1", queue="dump")
        await workflow(
            admin_db,
            "publish-1",
            "publish_schema",
            parent="run-1",
            queue="publish",
            partition="alpha",
            status="ERROR",
        )
        await workflow(admin_db, "other-1", "other", parent="run-1")

        result = await rows(
            admin_db,
            "SELECT id, run_id, name, status, queue_name, schema_name FROM admin.tasks ORDER BY id",
        )

        assert result == [
            ("dump-1", "run-1", "dump_task", "SUCCESS", "dump", None),
            ("publish-1", "run-1", "publish_schema", "ERROR", "publish", "alpha"),
        ]

    async def test_run_steps_lists_the_steps_of_runs_and_tasks(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A step shows its workflow, its name, its times, and whether it failed."""
        await workflow(admin_db, "run-1", "run_sync")
        await workflow(admin_db, "dump-1", "dump_task", parent="run-1")
        await workflow(admin_db, "other-1", "other")
        await admin_db.execute(
            b"""
            INSERT INTO dbos.operation_outputs
                (workflow_uuid, function_id, function_name, child_workflow_id, error, started_at_epoch_ms, completed_at_epoch_ms)
            VALUES
                ('run-1', 1, 'load_sync_config', NULL, NULL, 1700000000000, 1700000030000),
                ('run-1', 2, 'dump_task', 'dump-1', NULL, 1700000000000, 1700000030000),
                ('dump-1', 1, 'extract', NULL, 'boom', 1700000000000, 1700000030000),
                ('other-1', 1, 'ignored', NULL, NULL, 1700000000000, 1700000030000)
            """
        )

        result = await rows(
            admin_db,
            "SELECT run_id, workflow_id, function_id, function_name, child_workflow_id, failed, started_at, completed_at FROM admin.steps ORDER BY workflow_id, function_id",
        )

        assert result == [
            ("run-1", "dump-1", 1, "extract", None, True, START, END),
            ("run-1", "run-1", 1, "load_sync_config", None, False, START, END),
            ("run-1", "run-1", 2, "dump_task", "dump-1", False, START, END),
        ]

    async def test_run_logs_reads_the_portable_log_stream_per_run(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Log lines of a task appear under the run that started it, in stream order."""
        await workflow(admin_db, "run-1", "run_sync")
        await workflow(admin_db, "dump-1", "dump_task", parent="run-1")
        line = '{"time": "2023-11-14T22:13:20Z", "level": "INFO", "message": "Dump started", "fields": {"rows": 3}, "workflow_id": "dump-1", "schema": "alpha", "table": "p.d.one"}'
        await admin_db.execute(
            b"""
            INSERT INTO dbos.streams (workflow_uuid, key, value, "offset", serialization)
            VALUES ('dump-1', 'logs', %s, 0, 'portable_json'),
                   ('dump-1', 'other', '{}', 0, 'portable_json')
            """,
            (line,),
        )

        result = await rows(
            admin_db,
            "SELECT run_id, workflow_id, position, logged_at, level, message, fields, schema_name, table_name FROM admin.logs",
        )

        assert result == [
            (
                "run-1",
                "dump-1",
                0,
                START,
                "INFO",
                "Dump started",
                {"rows": 3},
                "alpha",
                "p.d.one",
            )
        ]


@pytest.mark.postgres
class TestQueues:
    """The queue views show the settings and the load of the queues."""

    async def test_queues_and_schedules_show_their_settings_and_load(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Queues list their settings and the workflows that wait or run, and schedules list theirs."""
        await admin_db.execute(
            b"INSERT INTO dbos.queues (name, concurrency, worker_concurrency, updated_at) VALUES ('q-test', 2, 4, %s)",
            (START_MS,),
        )
        await admin_db.execute(
            b"""
            INSERT INTO dbos.workflow_schedules
                (schedule_id, schedule_name, workflow_name, schedule, status, context, queue_name, cron_timezone)
            VALUES ('id-1', 's-test', 'run_sync', '0 3 * * *', 'ACTIVE', '', 'q-test', 'UTC')
            """
        )

        await workflow(admin_db, "w1", "run_sync", status="ENQUEUED", queue="q-test")
        await workflow(admin_db, "w2", "run_sync", status="ENQUEUED", queue="q-test")
        await workflow(admin_db, "w3", "run_sync", status="PENDING", queue="q-test")
        await workflow(admin_db, "w4", "run_sync", status="SUCCESS", queue="q-test")

        queues = await rows(
            admin_db,
            "SELECT name, concurrency, worker_concurrency, updated_at, enqueued, pending, delayed FROM admin.queues WHERE name = 'q-test'",
        )
        schedules = await rows(
            admin_db,
            "SELECT name, workflow_name, schedule, status, queue_name, timezone FROM admin.schedules WHERE name = 's-test'",
        )

        assert queues == [("q-test", 2, 4, START, 2, 1, 0)]
        assert schedules == [
            ("s-test", "run_sync", "0 3 * * *", "ACTIVE", "q-test", "UTC")
        ]


@pytest.mark.postgres
class TestErrors:
    """The errors view lists what the sync recorded."""

    async def test_errors_list_the_recorded_events_newest_first(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Errors keep their reason and fields, newest first."""
        await admin_db.execute(b"DELETE FROM data_proxy.errors")
        await admin_db.execute(
            b"""
            INSERT INTO data_proxy.errors (reason, fields, recorded_at)
            VALUES ('old', '{}', '2023-11-14T22:13:20Z'), ('new', '{"table": "t"}', '2023-11-15T22:13:20Z')
            """
        )

        result = await rows(admin_db, "SELECT reason, fields FROM admin.errors")

        assert result == [("new", {"table": "t"}), ("old", {})]
