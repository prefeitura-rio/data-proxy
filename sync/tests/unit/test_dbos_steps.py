"""Tests for the steps of the sync workflows."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from dbos import error as dbos_error

from sync.dbos import steps, utils
from sync.duckdb import DuckDB
from sync.ducklake import DuckLakePaths
from sync.models import (
    FullTable,
    PublicationResult,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
    SyncScope,
)
from sync.postgres import Postgres
from sync.settings import settings
from tests.fixtures.types import (
    Calls,
    Counted,
)
from tests.helpers.core import exactly
from tests.helpers.dbos import (
    publish,
    workflow_body,
)
from tests.helpers.state import DOCUMENT, dump_task, sync_config


class TestReconcileIdleSchemasStep:
    """The step reconciles the scope of the run against the whole config."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("scope", "schemas", "tables"),
        [
            pytest.param(
                SyncScope(),
                ["app", "other"],
                {"app": {"p.app.a", "p.app.b"}, "other": {"p.other.c"}},
                id="every-table",
            ),
            pytest.param(
                SyncScope(schemas=["app"]),
                ["app"],
                {"app": {"p.app.a", "p.app.b"}},
                id="one-schema",
            ),
            pytest.param(
                SyncScope(tables=["app.a"]),
                ["app"],
                {"app": {"p.app.a"}},
                id="one-table",
            ),
        ],
    )
    async def test_reconciles_only_the_scope_and_keeps_the_full_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        scope: SyncScope,
        schemas: list[str],
        tables: dict[str, set[str]],
    ) -> None:
        """Hand the full config to the reconcile, so the cleanup never drops a table outside the scope."""
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(
                    tables=[FullTable(name="p.app.a"), FullTable(name="p.app.b")]
                ),
                "other": SchemaConfig(tables=[FullTable(name="p.other.c")]),
            }
        )
        connection = object()

        @asynccontextmanager
        async def connect(_dsn: str) -> AsyncGenerator[object]:
            yield connection

        reconcile = AsyncMock(return_value=True)
        monkeypatch.setattr(Postgres, "connect", connect)
        monkeypatch.setattr(steps, "reconcile_views", reconcile)

        result = await workflow_body(steps.reconcile_idle_schemas)(
            config.model_dump_json(), scope
        )

        reconcile.assert_awaited_once()
        call = reconcile.await_args
        assert call is not None
        pg_conn, passed, schema_names, table_names = call.args
        assert isinstance(passed, SyncConfig)
        assert pg_conn is connection
        assert sorted(passed.schemas) == ["app", "other"]
        assert schema_names == schemas
        assert table_names == tables
        assert result is True


class TestSeedSchemasStep:
    """The seed reconciles the whole schema of a plan, and the stored signature skips the tables that did not change."""

    @pytest.mark.asyncio
    async def test_reconciles_every_table_of_a_planned_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plan that lists one table still reconciles the other tables of its schema, so one run applies a change of a template."""
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(
                    tables=[FullTable(name="p.app.a"), FullTable(name="p.app.b")]
                ),
                "other": SchemaConfig(tables=[FullTable(name="p.other.c")]),
            }
        )
        connection = object()

        @asynccontextmanager
        async def connect(_dsn: str) -> AsyncGenerator[object]:
            yield connection

        reconcile = AsyncMock(return_value=True)
        monkeypatch.setattr(Postgres, "connect", connect)
        monkeypatch.setattr(steps, "reconcile_views", reconcile)

        result = await workflow_body(steps.seed_schemas)(
            [
                SyncPlan(
                    schema_name="app",
                    signatures={"p.app.a": "s"},
                    paths={"p.app.a": ["a.parquet"]},
                )
            ],
            config.model_dump_json(),
        )

        reconcile.assert_awaited_once()
        call = reconcile.await_args
        assert call is not None
        pg_conn, passed, schema_names, table_names = call.args
        assert isinstance(passed, SyncConfig)
        assert pg_conn is connection
        assert sorted(passed.schemas) == ["app", "other"]
        assert schema_names == ["app"]
        assert table_names == {"app": {"p.app.a", "p.app.b"}}
        assert result is True


class TestReloadPostgrestStep:
    """The step tells PostgREST to reload through the primary."""

    @pytest.mark.asyncio
    async def test_notifies_postgrest_through_one_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opens the application connection and sends the notification on it."""
        connection = object()
        connections: list[object] = []

        @asynccontextmanager
        async def connect(dsn: str) -> AsyncGenerator[object]:
            connections.append(dsn)
            yield connection

        notify = AsyncMock()
        monkeypatch.setattr(Postgres, "connect", connect)
        monkeypatch.setattr(steps, "notify_postgrest", notify)

        await workflow_body(steps.reload_postgrest)()

        assert connections == [settings.PG_DATABASE_URL.get_secret_value()]
        notify.assert_awaited_once_with(connection)


class TestApplyDucklakeMaintenanceStep:
    """The maintenance step attaches the schema catalog of the configured location."""

    @pytest.mark.asyncio
    async def test_maintains_the_schema_catalog_and_returns_its_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = object()
        connections: list[object] = []
        maintain = AsyncMock(return_value=9)

        @asynccontextmanager
        async def connect() -> AsyncGenerator[object]:
            connections.append(connection)
            yield connection

        monkeypatch.setattr(DuckDB, "connect", connect)
        monkeypatch.setattr(steps, "apply_maintenance", maintain)

        document = sync_config([FullTable(name="p.d.t")]).model_dump_json()

        snapshot_id = await workflow_body(steps.apply_ducklake_maintenance)(
            "app", document
        )

        assert snapshot_id == 9
        assert connections == [connection]
        maintain.assert_awaited_once_with(
            connection, DuckLakePaths.for_schema("app"), False
        )


class TestExtractTaskLogs:
    """The extract step tells the console what each dump does."""

    @staticmethod
    def record(
        monkeypatch: pytest.MonkeyPatch,
    ) -> list[tuple[str, str, dict[str, object]]]:
        """Replace the log functions with recorders and the DuckDB connection with a stub."""
        lines: list[tuple[str, str, dict[str, object]]] = []

        def recorder(level: str) -> Callable[..., Awaitable[None]]:
            async def write(message: str, /, **fields: object) -> None:
                lines.append((level, message, fields))

            return write

        @asynccontextmanager
        async def connect() -> AsyncGenerator[object]:
            yield object()

        monkeypatch.setattr(steps.logs, "info", recorder("info"))
        monkeypatch.setattr(steps.logs, "warning", recorder("warning"))
        monkeypatch.setattr(DuckDB, "connect", connect)
        return lines

    @pytest.mark.asyncio
    async def test_logs_the_source_and_each_partition_of_a_dump(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lines of one dump say the columns, then the rows of each partition."""
        task = dump_task()
        lines = self.record(monkeypatch)

        async def extract(
            connection: object, task: object, report: Callable[..., Awaitable[None]]
        ) -> None:
            await report("Source described", columns=3)
            await report(
                "Partition written", partition_number=1, partition_count=1, rows=10
            )

        monkeypatch.setattr(steps, "run_extraction", extract)

        await workflow_body(steps.extract_task)(task)

        assert [
            (message, fields.get("columns"), fields.get("rows"))
            for _, message, fields in lines
        ] == [
            ("Extraction started", None, None),
            ("Source described", 3, None),
            ("Partition written", None, 10),
            ("Extraction completed", None, None),
        ]
        assert {fields["task_id"] for _, _, fields in lines} == {task.task_id}

    @pytest.mark.asyncio
    async def test_warns_with_the_error_of_a_failed_attempt_and_raises_it_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each retry of a failing dump leaves a line, and the step still fails."""
        task = dump_task()
        lines = self.record(monkeypatch)

        async def extract(
            connection: object, task: object, report: Callable[..., Awaitable[None]]
        ) -> None:
            raise RuntimeError("write failed")

        monkeypatch.setattr(steps, "run_extraction", extract)

        with pytest.raises(RuntimeError, match=exactly("write failed")):
            await workflow_body(steps.extract_task)(task)

        level, message, fields = lines[-1]
        assert (level, message) == ("warning", "Extraction attempt failed")
        assert str(fields["error"]).startswith("RuntimeError: write failed")


class TestExtractTaskCancellation:
    """A dump that a run timeout or a console user cancelled stops at its next partition."""

    @staticmethod
    def stub_status(monkeypatch: pytest.MonkeyPatch, statuses: list[str]) -> AsyncMock:
        """Answer each status check with the next status of the list, and the last one after that."""
        answers = iter(statuses)
        last = statuses[-1]

        async def status(workflow_id: str) -> SimpleNamespace:
            return SimpleNamespace(status=next(answers, last))

        read = AsyncMock(side_effect=status)
        monkeypatch.setattr(
            steps,
            "DBOS",
            SimpleNamespace(workflow_id="dump-1", get_workflow_status_async=read),
        )
        return read

    @pytest.mark.parametrize(
        ("statuses", "written", "cancelled"),
        [
            pytest.param(["PENDING"], 5, False, id="never-cancelled"),
            pytest.param(
                ["PENDING", "PENDING", "CANCELLED"],
                3,
                True,
                id="cancelled-on-the-third",
            ),
            pytest.param(["CANCELLED"], 1, True, id="cancelled-at-once"),
        ],
    )
    @pytest.mark.asyncio
    async def test_stops_after_the_partition_that_it_wrote_when_the_dump_is_cancelled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        statuses: list[str],
        written: int,
        cancelled: bool,
    ) -> None:
        lines = TestExtractTaskLogs.record(monkeypatch)
        self.stub_status(monkeypatch, statuses)
        done: list[int] = []

        async def extract(
            connection: object, task: object, report: Callable[..., Awaitable[None]]
        ) -> None:
            for number in range(1, 6):
                done.append(number)
                await report("Partition written", partition_number=number)

        monkeypatch.setattr(steps, "run_extraction", extract)

        if cancelled:
            with pytest.raises(dbos_error.DBOSWorkflowCancelledError):
                await workflow_body(steps.extract_task)(dump_task())
        else:
            await workflow_body(steps.extract_task)(dump_task())

        messages = [message for _, message, _ in lines]
        assert len(done) == written
        assert ("Extraction cancelled" in messages) is cancelled
        assert ("Extraction completed" in messages) is not cancelled
        assert "Extraction attempt failed" not in messages

    @pytest.mark.asyncio
    async def test_does_not_retry_a_cancelled_dump(self) -> None:
        """The error that DBOS raises for a cancel is not a backend failure, so the step never retries it."""
        error = dbos_error.DBOSWorkflowCancelledError("cancelled")

        assert utils.retry_transient(error) is False


class TestStepMetrics:
    """The step that does the work counts it, once, when the work ends."""

    @pytest.fixture
    def counter(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Counted]:
        """Replace the counters of the steps, and the connections that the steps open."""
        counters = {
            name: Counted()
            for name in ("dump_tasks_total", "publish_tables_total", "seed_runs_total")
        }
        for name, counted in counters.items():
            monkeypatch.setattr(steps.metrics, name, counted)

        @asynccontextmanager
        async def connect(*_: object) -> AsyncGenerator[object]:
            yield object()

        monkeypatch.setattr(DuckDB, "connect", connect)
        monkeypatch.setattr(Postgres, "connect", connect)
        return counters

    @pytest.mark.asyncio
    async def test_counts_a_dump_when_it_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, counter: dict[str, Counted]
    ) -> None:
        """Count the dump after the extraction, so a retry counts once."""
        monkeypatch.setattr(steps, "run_extraction", AsyncMock())

        await workflow_body(steps.extract_task)(dump_task(table="p.d.t"))

        assert counter["dump_tasks_total"].adds == [
            (1, {"table": "p.d.t", "schema": "app", "status": "success"})
        ]

    @pytest.mark.asyncio
    async def test_counts_nothing_for_a_failed_attempt(
        self, monkeypatch: pytest.MonkeyPatch, counter: dict[str, Counted]
    ) -> None:
        """The failure is counted by the step that records it, not by each attempt."""
        monkeypatch.setattr(
            steps, "run_extraction", AsyncMock(side_effect=RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError, match=exactly("boom")):
            await workflow_body(steps.extract_task)(dump_task())

        assert counter["dump_tasks_total"].adds == []

    @pytest.mark.asyncio
    async def test_counts_a_dump_that_failed(
        self, monkeypatch: pytest.MonkeyPatch, counter: dict[str, Counted]
    ) -> None:
        """Count the failure where the step records it."""
        monkeypatch.setattr(steps, "emit_error", AsyncMock())

        await workflow_body(steps.record_dump_failure)(dump_task(table="p.d.t"), "boom")

        assert counter["dump_tasks_total"].adds == [
            (1, {"table": "p.d.t", "schema": "app", "status": "error"})
        ]

    @pytest.mark.asyncio
    async def test_counts_a_seed_for_each_schema_plan(
        self, monkeypatch: pytest.MonkeyPatch, counter: dict[str, Counted]
    ) -> None:
        """One count for each plan that the seed reconciled."""
        monkeypatch.setattr(steps, "reconcile_views", AsyncMock(return_value=False))

        await workflow_body(steps.seed_schemas)(
            [SyncPlan(schema_name="app"), SyncPlan(schema_name="one")], DOCUMENT
        )

        assert counter["seed_runs_total"].adds == [
            (1, {"schema": "app", "status": "success"}),
            (1, {"schema": "one", "status": "success"}),
        ]

    @pytest.mark.asyncio
    async def test_counts_the_tables_that_published_and_the_ones_that_did_not(
        self, monkeypatch: pytest.MonkeyPatch, counter: dict[str, Counted]
    ) -> None:
        """Two tables in the plan and one published give one success and one error."""
        plan = SyncPlan(
            schema_name="app",
            signatures={"a": "s", "b": "s"},
            paths={"a": ["a.parquet"], "b": ["b.parquet"]},
        )
        result = PublicationResult(plan=plan, published_tables={"a"}, snapshot_id=3)
        monkeypatch.setattr(steps, "publish_schema", AsyncMock(return_value=result))

        await workflow_body(steps.commit_ducklake_snapshot)(plan, set(), DOCUMENT)

        assert counter["publish_tables_total"].adds == [
            (1, {"schema": "app", "status": "success"}),
            (1, {"schema": "app", "status": "error"}),
        ]


class TestPublishSchemaOrder:
    async def test_maintains_the_schema_after_the_snapshot_and_returns_it(
        self, calls: Calls
    ) -> None:
        """Maintain the schema after the snapshot, and return the snapshot that the maintenance gives."""
        snapshot_id = await publish(SyncPlan(schema_name="app"))

        assert calls.names == [
            "commit_ducklake_snapshot",
            "apply_ducklake_maintenance",
        ]
        assert snapshot_id == 45

    async def test_returns_no_snapshot_when_nothing_was_published(
        self, calls: Calls
    ) -> None:
        calls.snapshot_id = None

        assert await publish(SyncPlan(schema_name="app")) is None
        assert "apply_ducklake_maintenance" not in calls.names

    async def test_returns_the_published_snapshot_when_maintenance_fails(
        self, calls: Calls
    ) -> None:
        calls.maintenance_error = RuntimeError("boom")

        snapshot_id = await publish(SyncPlan(schema_name="app"))

        assert calls.names[-1] == "apply_ducklake_maintenance"
        assert snapshot_id == 42
