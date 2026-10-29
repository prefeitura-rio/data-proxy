import asyncio
import importlib
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
from dbos import DBOS
from dbos import error as dbos_error
from psycopg import OperationalError
from pydantic import ValidationError

from sync.constants import PUBLISH_QUEUE
from sync.dbos import main as dbos_main
from sync.dbos import steps, utils, workflows
from sync.duckdb import DuckDB
from sync.ducklake import DuckLakePaths, table_prefix
from sync.models import (
    DumpResult,
    DumpStatus,
    FullTable,
    LoadedConfig,
    PublicationResult,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
    SyncScope,
    SyncWork,
)
from sync.postgres import Postgres
from sync.settings import settings
from sync.state import ScopeRow
from sync.utils import sha256_hex
from tests.fixtures.types import Calls
from tests.helpers import (
    DOCUMENT,
    RecordedOptions,
    catalog_commit_error,
    dump_task,
    enqueue_label,
    publish,
    run_sync,
    stub_sync_run,
    sync_config,
    workflow_body,
)

WORKFLOWS = [
    name
    for name, workflow in vars(workflows).items()
    if callable(workflow) and workflow.__module__ == workflows.__name__
]


class TestWorkflowAnnotations:
    """DBOS reads the annotations of a workflow when it runs the workflow."""

    @pytest.mark.parametrize("name", WORKFLOWS)
    def test_every_annotation_names_something_that_exists_at_run_time(
        self, name: str
    ) -> None:
        """An import that only a type checker sees makes the run fail with a NameError."""
        body = workflow_body(getattr(workflows, name))

        assert inspect.get_annotations(body, eval_str=True) is not None


class TestTransientRetryClassification:
    """TransientRetryClassification behavior tests."""

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            pytest.param(RuntimeError("temporary"), True, id="a-runtime-error"),
            pytest.param(ConnectionError("reset"), True, id="a-lost-connection"),
            pytest.param(OperationalError("closed"), True, id="a-database-error"),
            pytest.param(ValueError("invalid"), False, id="invalid-input"),
            pytest.param(KeyError("app"), False, id="a-missing-key-is-a-bug"),
            pytest.param(TypeError("bad"), False, id="a-wrong-type-is-a-bug"),
            pytest.param(AttributeError("bad"), False, id="a-missing-attribute"),
        ],
    )
    def test_retries_a_backend_failure_and_not_a_bug(
        self, error: Exception, expected: bool
    ) -> None:
        """Retry what can pass at the next attempt, and fail at once on what cannot."""
        assert utils.retry_transient(error) is expected

    def test_classifies_cancellation_as_permanent(self) -> None:
        """Classify cancellation as permanent."""
        assert not utils.retry_transient(asyncio.CancelledError())


class TestCatalogLockRetryClassification:
    """retry_catalog_locked retries only the DuckLake catalog lock error."""

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            pytest.param(
                catalog_commit_error("database is locked"),
                True,
                id="the lock error of a DuckLake commit",
            ),
            pytest.param(
                catalog_commit_error("UNIQUE constraint failed"),
                False,
                id="another DuckLake commit error",
            ),
            pytest.param(RuntimeError("temporary"), False, id="a runtime error"),
            pytest.param(
                RuntimeError("database is locked"),
                False,
                id="the same message from another error type",
            ),
            pytest.param(asyncio.CancelledError(), False, id="a cancellation"),
        ],
    )
    def test_retries_only_the_catalog_lock_error(
        self, error: BaseException, expected: bool
    ) -> None:
        """Retries only the catalog lock error."""
        assert utils.retry_catalog_locked(error) is expected


class TestRunSyncReloadsPostgrest:
    """run_sync reloads PostgREST once, only when the view set changed."""

    @pytest.mark.parametrize(
        ("postgrest_reload_required", "snapshot_id", "expected"),
        [
            pytest.param(False, None, [], id="nothing-changed"),
            pytest.param(False, 7, [], id="snapshot-published-without-view-change"),
            pytest.param(
                True, None, ["reload_postgrest"], id="view-change-without-snapshot"
            ),
            pytest.param(True, 7, ["reload_postgrest"], id="snapshot-and-view-change"),
        ],
    )
    @pytest.mark.asyncio
    async def test_reloads_postgrest_only_after_a_view_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        postgrest_reload_required: bool,
        snapshot_id: int | None,
        expected: list[str],
    ) -> None:
        """Reloads postgrest only after a view change."""
        calls, events = stub_sync_run(
            monkeypatch,
            postgrest_reload_required=postgrest_reload_required,
            snapshot_id=snapshot_id,
        )

        await run_sync()

        assert calls == expected
        assert events == ["enqueue:app", "wait:app"]

    @pytest.mark.asyncio
    async def test_waits_for_every_schema_publisher_before_it_reloads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wait for both publishers, then reload once."""
        calls, events = stub_sync_run(
            monkeypatch,
            postgrest_reload_required=True,
            snapshot_ids={"one": 3, "two": None},
            plans=[SyncPlan(schema_name="one"), SyncPlan(schema_name="two")],
        )

        await run_sync()

        assert calls == ["reload_postgrest"]
        assert events == ["enqueue:one", "enqueue:two", "wait:one", "wait:two"]

    @pytest.mark.asyncio
    async def test_enqueues_all_dumps_before_waiting_for_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Queue every dump child workflow before durable fan-in starts."""
        first = dump_task(table="p.d.first")
        second = dump_task(table="p.d.second")
        calls, events = stub_sync_run(
            monkeypatch,
            postgrest_reload_required=False,
            snapshot_id=None,
            tasks=[first, second],
        )

        await run_sync()

        assert calls == []
        assert events == [
            "enqueue:p.d.first",
            "enqueue:p.d.second",
            "wait:p.d.first",
            "wait:p.d.second",
            "enqueue:app",
            "wait:app",
        ]


class TestRunSyncRecordsItsScope:
    """The run tells the database which schemas it covers, so the console can scope it."""

    @pytest.mark.asyncio
    async def test_records_the_schemas_of_the_plans_and_each_dump_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One row for each schema of the run, and one for each dump task with its table."""
        stub_sync_run(
            monkeypatch,
            postgrest_reload_required=False,
            snapshot_id=None,
            plans=[SyncPlan(schema_name="app"), SyncPlan(schema_name="other")],
            tasks=[dump_task(table="p.d.first"), dump_task(table="p.d.second")],
        )
        record = AsyncMock()
        monkeypatch.setattr(workflows, "record_scope", record)

        await run_sync()

        record.assert_awaited_once_with(
            "run",
            ["app", "other"],
            [
                ("workflow:p.d.first", "app", "p.d.first"),
                ("workflow:p.d.second", "app", "p.d.second"),
            ],
        )


class TestRecordScopeStep:
    """The step stores the scope rows through the application connection."""

    @pytest.mark.asyncio
    async def test_inserts_the_rows_of_the_run_and_its_tasks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The run rows have an empty table, and the task rows name theirs."""
        connection = object()
        stored = AsyncMock()

        @asynccontextmanager
        async def connect(dsn: str) -> AsyncGenerator[object]:
            yield connection

        monkeypatch.setattr(Postgres, "connect", connect)
        monkeypatch.setattr(steps, "write_scope", stored)

        await workflow_body(steps.record_scope)(
            "run", ["app"], [("task-1", "app", "p.d.first")]
        )

        stored.assert_awaited_once_with(
            connection,
            [
                ScopeRow("run", "run", "app", ""),
                ScopeRow("task-1", "run", "app", "p.d.first"),
            ],
        )


class TestLoadSyncConfig:
    """load_sync_config step behavior tests."""

    @pytest.fixture
    def stored(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """Stand in for the database and return the reader of the stored configuration."""

        @asynccontextmanager
        async def connect(dsn: str) -> AsyncGenerator[None]:
            yield

        read = AsyncMock(return_value=None)
        monkeypatch.setattr(Postgres, "connect", connect)
        monkeypatch.setattr(steps, "read_sync_config", read)
        return read

    async def test_takes_the_configuration_from_the_database(
        self, stored: AsyncMock
    ) -> None:
        """Use the document of the tables, and name its revision by the digest of the text."""
        stored.return_value = DOCUMENT

        loaded = await workflow_body(steps.load_sync_config)()

        assert loaded == LoadedConfig(
            revision=sha256_hex(DOCUMENT)[:12], document=DOCUMENT
        )

    async def test_rejects_a_document_that_the_engine_cannot_use(
        self, stored: AsyncMock
    ) -> None:
        """Fail the run at its start, before any work."""
        stored.return_value = '{"schemas": {"app": 1}}'

        with pytest.raises(ValidationError):
            await workflow_body(steps.load_sync_config)()

    async def test_fails_when_the_database_fails(self, stored: AsyncMock) -> None:
        """Raise, so that DBOS retries the step."""
        stored.side_effect = OperationalError("down")

        with pytest.raises(OperationalError):
            await workflow_body(steps.load_sync_config)()


class TestRunSyncUsesOneConfig:
    """run_sync loads the config once and hands the same document to every step."""

    @pytest.mark.asyncio
    async def test_hands_the_loaded_document_to_planning_seeding_and_publishing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pass the document that the first step returned, never a second copy."""
        plan = SyncPlan(schema_name="app")
        publishes: list[tuple[str, object]] = []
        stub_sync_run(
            monkeypatch,
            postgrest_reload_required=False,
            snapshot_id=None,
            publishes=publishes,
        )
        load = AsyncMock(return_value=LoadedConfig(revision="9", document=DOCUMENT))
        build = AsyncMock(return_value=SyncWork(plans=[plan], tasks=[]))
        seed = AsyncMock(return_value=False)
        monkeypatch.setattr(workflows, "load_sync_config", load)
        monkeypatch.setattr(workflows, "build_sync_work", build)
        monkeypatch.setattr(workflows, "seed_schemas", seed)

        await run_sync()

        load.assert_awaited_once_with()
        build.assert_awaited_once_with("run", DOCUMENT, SyncScope())
        seed.assert_awaited_once_with([plan], DOCUMENT)

    @pytest.mark.asyncio
    async def test_gives_each_publisher_only_its_own_schema_and_failed_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A schema with many tables must not make the input of every other publisher big."""
        stub_sync_run(
            monkeypatch,
            postgrest_reload_required=False,
            plans=[SyncPlan(schema_name="one"), SyncPlan(schema_name="two")],
            tasks=[
                dump_task(table="one.t").model_copy(update={"target_schema": "one"}),
                dump_task(table="two.t").model_copy(update={"target_schema": "two"}),
            ],
        )
        published: dict[str, tuple[object, object]] = {}

        async def enqueue(
            queue: str, workflow: object, *args: object
        ) -> SimpleNamespace:
            label = enqueue_label(workflow, args)
            outcome: DumpResult | None = None

            if workflow is workflows.publish_schema:
                published[label] = (args[2], args[3])
            elif label == "one.t":
                outcome = DumpResult(
                    status=DumpStatus.FAILURE, failed_paths={"s3://b/one"}
                )
            else:
                outcome = DumpResult()

            return SimpleNamespace(
                get_result=AsyncMock(return_value=outcome), workflow_id=label
            )

        monkeypatch.setattr(workflows.DBOS, "enqueue_workflow_async", enqueue)

        await run_sync()

        assert {name: failed for name, (failed, _) in published.items()} == {
            "one": {"s3://b/one"},
            "two": set(),
        }
        document = published["one"][1]
        assert isinstance(document, str)
        assert set(SyncConfig.model_validate_json(document).schemas) == {"one"}

    @pytest.mark.asyncio
    async def test_plans_only_the_scope_of_an_on_demand_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hand the scope of the request to planning, and the whole document to seeding."""
        plan = SyncPlan(schema_name="app")
        stub_sync_run(monkeypatch, postgrest_reload_required=False, snapshot_id=None)
        build = AsyncMock(return_value=SyncWork(plans=[plan], tasks=[]))
        seed = AsyncMock(return_value=False)
        monkeypatch.setattr(workflows, "build_sync_work", build)
        monkeypatch.setattr(workflows, "seed_schemas", seed)

        await run_sync({"schemas": ["app"], "tables": ["other.t"]})

        build.assert_awaited_once_with(
            "run", DOCUMENT, SyncScope(schemas=["app"], tables=["other.t"])
        )
        seed.assert_awaited_once_with([plan], DOCUMENT)

    @pytest.mark.asyncio
    async def test_publishes_every_schema_on_one_queue_partitioned_by_its_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Use the publish queue, so a schema added later needs no new queue."""
        publishes: list[tuple[str, object]] = []
        stub_sync_run(
            monkeypatch,
            postgrest_reload_required=False,
            snapshot_ids={"one": None, "two": None},
            plans=[SyncPlan(schema_name="one"), SyncPlan(schema_name="two")],
            publishes=publishes,
        )

        await run_sync()

        assert publishes == [(PUBLISH_QUEUE, "one"), (PUBLISH_QUEUE, "two")]


class TestRunSyncWithoutChanges:
    """A run that plans nothing still reconciles the serving views of its scope."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reload_required", [True, False])
    async def test_reconciles_the_views_and_reloads_postgrest_only_when_they_changed(
        self, monkeypatch: pytest.MonkeyPatch, reload_required: bool
    ) -> None:
        """Reconcile with the loaded document and the scope, and skip dump, publish and finalize."""
        calls, events = stub_sync_run(
            monkeypatch, postgrest_reload_required=False, snapshot_id=None
        )
        monkeypatch.setattr(
            workflows,
            "build_sync_work",
            AsyncMock(return_value=SyncWork(plans=[], tasks=[])),
        )
        reconcile = AsyncMock(return_value=reload_required)
        finalize = AsyncMock()
        monkeypatch.setattr(workflows, "reconcile_idle_schemas", reconcile)
        monkeypatch.setattr(workflows, "finalize_run", finalize)

        await run_sync({"schemas": ["app"]})

        reconcile.assert_awaited_once_with(DOCUMENT, SyncScope(schemas=["app"]))
        assert calls == (["reload_postgrest"] if reload_required else [])
        assert events == []
        finalize.assert_not_awaited()


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


CONNECTION = object()


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
        """Maintains the schema catalog and returns its snapshot."""
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
            await report("Partition written", part=1, of=1, rows=10)

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

        with pytest.raises(RuntimeError, match="write failed"):
            await workflow_body(steps.extract_task)(task)

        level, message, fields = lines[-1]
        assert (level, message) == ("warning", "Extraction attempt failed")
        assert str(fields["error"]).startswith("RuntimeError: write failed")


class Counted:
    """Stand-in for an OpenTelemetry counter that remembers each increase."""

    def __init__(self) -> None:
        """Start with no increase."""
        self.adds: list[tuple[int, dict[str, str]]] = []

    def add(self, amount: int, attributes: dict[str, str]) -> None:
        """Remember one increase."""
        self.adds.append((amount, attributes))


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

        with pytest.raises(RuntimeError, match="boom"):
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
    """publish_schema workflow behavior tests."""

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
        """Returns no snapshot when nothing was published."""
        calls.snapshot_id = None

        assert await publish(SyncPlan(schema_name="app")) is None
        assert "apply_ducklake_maintenance" not in calls.names

    async def test_returns_the_published_snapshot_when_maintenance_fails(
        self, calls: Calls
    ) -> None:
        """Returns the published snapshot when maintenance fails."""
        calls.maintenance_error = RuntimeError("boom")

        snapshot_id = await publish(SyncPlan(schema_name="app"))

        assert calls.names[-1] == "apply_ducklake_maintenance"
        assert snapshot_id == 42


class RecordingDbos:
    """Stand-in for the DBOS class that records how the application sets itself up."""

    queues: ClassVar[list[tuple[str, str]]] = []
    concurrency: ClassVar[dict[str, object]] = {}
    enqueued: ClassVar[list[tuple[str, str]]] = []
    listened: ClassVar[list[str]] = []
    deduplicated: ClassVar[bool] = False
    options: ClassVar[list[dict[str, object]]] = []

    def __init__(self, **_: object) -> None:
        """Accept the configuration."""

    @classmethod
    def listen_queues(cls, queues: list[str]) -> None:
        """Record the queues to listen to."""
        cls.listened = queues

    @classmethod
    def launch(cls) -> None:
        """Ignore the launch."""

    @classmethod
    async def register_queue_async(
        cls,
        name: str,
        *,
        on_conflict: str = "always_update",
        concurrency: object = None,
        **_: object,
    ) -> None:
        """Record the queue, how it treats an existing one, and its concurrency."""
        cls.queues.append((name, on_conflict))
        cls.concurrency[name] = concurrency

    @classmethod
    async def enqueue_workflow_async(
        cls, queue: str, workflow: Callable[..., object]
    ) -> None:
        """Record the workflow that startup queued, and refuse it when the test says that one waits."""
        cls.options.append(RecordedOptions.active)

        if cls.deduplicated:
            raise dbos_error.DBOSQueueDeduplicatedError("w", queue, "id")

        cls.enqueued.append((queue, workflow.__name__))


class ReturnsAtOnce:
    """An event that never blocks, so that serve returns."""

    async def wait(self) -> None:
        """Return at once."""


@pytest.mark.usefixtures("recording")
class TestServe:
    """Startup creates the queues and the schedule when they are missing, and never overwrites them."""

    @pytest.fixture
    def recording(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace DBOS, and reset what it recorded."""
        RecordingDbos.queues = []
        RecordingDbos.concurrency = {}
        RecordingDbos.enqueued = []
        RecordingDbos.listened = []
        RecordingDbos.deduplicated = False
        RecordingDbos.options = []
        monkeypatch.setattr(dbos_main, "DBOS", RecordingDbos)
        monkeypatch.setattr(dbos_main, "SetEnqueueOptions", RecordedOptions)
        monkeypatch.setattr(dbos_main, "Event", ReturnsAtOnce)

    @pytest.mark.asyncio
    async def test_leaves_the_stored_queues_alone(self) -> None:
        """Seed each queue once, so a limit that the console changed survives a restart."""
        await dbos_main.serve()

        assert RecordingDbos.queues == [
            ("sync", "never_update"),
            ("dump", "never_update"),
            ("publish", "never_update"),
            ("proxy", "never_update"),
            ("provision", "never_update"),
            ("control", "never_update"),
            ("schedules", "never_update"),
        ]

    @pytest.mark.asyncio
    async def test_listens_to_every_queue_by_default(self) -> None:
        """A worker of the chart runs every kind of work."""
        await dbos_main.serve()

        assert RecordingDbos.listened == [
            "sync",
            "dump",
            "publish",
            "proxy",
            "provision",
            "control",
            "schedules",
        ]

    @pytest.mark.asyncio
    async def test_listens_only_to_the_queues_that_the_setting_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The test stack runs a worker that only controls runs and schedules."""
        monkeypatch.setattr(settings, "LISTEN_QUEUES", "control")

        await dbos_main.serve()

        assert RecordingDbos.listened == ["control"]

    @pytest.mark.asyncio
    async def test_runs_one_provision_at_a_time(self) -> None:
        """A change during a run queues the next run, which must wait for the running one to end."""
        await dbos_main.serve()

        assert RecordingDbos.concurrency["provision"] == 1

    @pytest.mark.asyncio
    async def test_runs_one_reconcile_of_the_schedules_at_a_time(self) -> None:
        """Two reconciles at once would both create the schedule of a new schema."""
        await dbos_main.serve()

        assert RecordingDbos.concurrency["schedules"] == 1

    @pytest.mark.asyncio
    async def test_queues_one_reconcile_of_the_schedules(self) -> None:
        """Make one schedule for each schema, and drop the old global one, in a queue that runs one reconcile at a time."""
        await dbos_main.serve()

        assert RecordingDbos.enqueued == [("schedules", "reconcile_schedules")]

    @pytest.mark.asyncio
    async def test_asks_dbos_to_queue_one_reconcile_at_a_time(self) -> None:
        """The deduplication id lets DBOS refuse a second reconcile while the first waits."""
        await dbos_main.serve()

        assert RecordingDbos.options == [{"deduplication_id": "reconcile_schedules"}]

    @pytest.mark.asyncio
    async def test_starts_when_dbos_refuses_the_reconcile_because_one_waits(
        self,
    ) -> None:
        """Two workers that start together queue one reconcile, and the second one goes on."""
        RecordingDbos.deduplicated = True

        await dbos_main.serve()

        assert RecordingDbos.enqueued == []


class TestListenedQueues:
    """The setting LISTEN_QUEUES picks the queues that one worker listens to."""

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            pytest.param("", ["a", "b", "c"], id="empty means every queue"),
            pytest.param("  ", ["a", "b", "c"], id="blank means every queue"),
            pytest.param("b", ["b"], id="one queue"),
            pytest.param(
                "c, a", ["a", "c"], id="the order of the queues, not of the setting"
            ),
        ],
    )
    def test_picks_the_queues(self, configured: str, expected: list[str]) -> None:
        """Pick the queues that the setting names."""
        assert utils.listened_queues(["a", "b", "c"], configured) == expected

    def test_refuses_an_unknown_queue(self) -> None:
        """A typo must stop the worker, not leave work in a queue that nobody reads."""
        with pytest.raises(ValueError, match="Unknown queues: z"):
            utils.listened_queues(["a", "b"], "a,z")


class TestMain:
    """Entrypoint behavior tests."""

    def test_runs_the_application_on_uvloop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Run the application on uvloop, so DBOS adopts it as its main loop."""
        loops: list[str] = []

        async def serve() -> None:
            loops.append(type(asyncio.get_running_loop()).__module__)

        monkeypatch.setattr(dbos_main, "serve", serve)

        dbos_main.main()

        assert loops == ["uvloop"]


@pytest.mark.usefixtures("standbys")
class TestNotifyProxies:
    """The reload workflow asks every proxy pod to read the configuration again."""

    @pytest.fixture
    def standbys(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """Stand in for the database and return the wait for the standbys."""

        @asynccontextmanager
        async def connect(dsn: str) -> AsyncGenerator[object]:
            yield CONNECTION

        wait = AsyncMock()
        monkeypatch.setattr(Postgres, "connect", connect)
        monkeypatch.setattr(steps, "wait_for_standbys", wait)
        return wait

    async def test_resolves_the_pods_and_reloads_each_of_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The step passes the addresses of the headless service to the notifier."""
        reload = AsyncMock()
        monkeypatch.setattr(
            settings, "PROXY_ADMIN_URL", "http://proxy-admin:8081/reload"
        )
        monkeypatch.setattr(
            steps, "proxy_addresses", AsyncMock(return_value=["10.0.0.1"])
        )
        monkeypatch.setattr(steps, "reload_proxies", reload)

        await workflow_body(steps.notify_proxies)()

        assert reload.await_args is not None
        assert reload.await_args.args[1:] == (
            "http://proxy-admin:8081/reload",
            ["10.0.0.1"],
        )

    async def test_waits_for_the_standbys_before_it_asks_the_pods(
        self, monkeypatch: pytest.MonkeyPatch, standbys: AsyncMock
    ) -> None:
        """The proxy reads the configuration from a standby, so the standbys must have replayed the change first."""
        events: list[str] = []

        async def wait(pg_conn: object) -> None:
            assert pg_conn is CONNECTION
            events.append("wait")

        async def reload(*_: object) -> None:
            events.append("reload")

        standbys.side_effect = wait
        monkeypatch.setattr(
            settings, "PROXY_ADMIN_URL", "http://proxy-admin:8081/reload"
        )
        monkeypatch.setattr(
            steps, "proxy_addresses", AsyncMock(return_value=["10.0.0.1"])
        )
        monkeypatch.setattr(steps, "reload_proxies", reload)

        await workflow_body(steps.notify_proxies)()

        assert events == ["wait", "reload"]

    async def test_does_nothing_without_an_address_for_the_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment without a proxy has nobody to notify."""
        resolve = AsyncMock()
        monkeypatch.setattr(settings, "PROXY_ADMIN_URL", None)
        monkeypatch.setattr(steps, "proxy_addresses", resolve)

        await workflow_body(steps.notify_proxies)()

        resolve.assert_not_awaited()


class TestControlWorkflows:
    """The workflows that the console enqueues to cancel or resume a run, and to pause or resume a schedule."""

    @pytest.mark.asyncio
    async def test_resume_run_resumes_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """resume_run puts the run back in the sync queue, so the sync concurrency still applies."""
        resume = AsyncMock()
        monkeypatch.setattr(DBOS, "resume_workflow_async", resume)

        await workflow_body(workflows.resume_run)("run-1")

        resume.assert_awaited_once_with("run-1", queue_name="sync")


class TestScheduleWorkflows:
    """The workflows that keep one schedule for each schema, and that change the cron of one."""

    @staticmethod
    def schedule(name: str, **fields: object) -> dict[str, object]:
        """Return a stored schedule the way DBOS gives it."""
        stored: dict[str, object] = {
            "schedule_name": name,
            "schedule": "0 2 * * *",
            "status": "ACTIVE",
            "context": {"schemas": [name.removeprefix("sync-")], "tables": []},
            "queue_name": "sync",
            "cron_timezone": None,
            "automatic_backfill": False,
        }
        return stored | fields

    @staticmethod
    def stub_dbos(
        monkeypatch: pytest.MonkeyPatch,
        stored: list[dict[str, object]],
        refused_cron: str | None = None,
    ) -> list[tuple[str, str | dict[str, object]]]:
        """Replace the schedule calls of DBOS with a record of what the workflow asks.

        A create with the refused cron raises, as DBOS does for a cron that it cannot read.
        """
        calls: list[tuple[str, str | dict[str, object]]] = []

        async def list_schedules(**_: object) -> list[dict[str, object]]:
            return stored

        async def create_schedule(**fields: object) -> None:
            calls.append(("create", fields))

        async def delete_schedule(name: str) -> None:
            calls.append(("delete", name))

        monkeypatch.setattr(DBOS, "list_schedules_async", list_schedules)
        monkeypatch.setattr(DBOS, "create_schedule_async", create_schedule)
        monkeypatch.setattr(DBOS, "delete_schedule_async", delete_schedule)

        def get_schedule(name: str) -> dict[str, object] | None:
            return next((s for s in stored if s["schedule_name"] == name), None)

        def create_schedule_sync(**fields: object) -> None:
            calls.append(("create", fields))

            if fields["schedule"] == refused_cron:
                raise dbos_error.DBOSException("refused")

        def delete_schedule_sync(name: str) -> None:
            calls.append(("delete", name))

        def pause_schedule(name: str) -> None:
            calls.append(("pause", name))

        monkeypatch.setattr(DBOS, "get_schedule", get_schedule)
        monkeypatch.setattr(DBOS, "create_schedule", create_schedule_sync)
        monkeypatch.setattr(DBOS, "delete_schedule", delete_schedule_sync)
        monkeypatch.setattr(DBOS, "pause_schedule", pause_schedule)
        return calls

    @staticmethod
    def stub_config(monkeypatch: pytest.MonkeyPatch, schemas: list[str]) -> None:
        """Make the workflow read a configuration with these schemas."""
        document = SyncConfig(
            schemas={name: SchemaConfig() for name in schemas}
        ).model_dump_json()
        monkeypatch.setattr(
            workflows,
            "load_sync_config",
            AsyncMock(return_value=LoadedConfig(revision="r", document=document)),
        )

    async def test_reconcile_makes_a_schedule_for_a_schema_that_has_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A new schema runs at the default cron, for its own tables, in the queue of the sync runs."""
        calls = self.stub_dbos(monkeypatch, [self.schedule("sync-hr")])
        self.stub_config(monkeypatch, ["hr", "sales"])
        monkeypatch.setattr(settings, "SYNC_SCHEDULE", "30 3 * * *")

        await workflow_body(workflows.reconcile_schedules)()

        assert calls == [
            (
                "create",
                {
                    "schedule_name": "sync-sales",
                    "workflow_fn": workflows.run_sync,
                    "schedule": "30 3 * * *",
                    "context": {"schemas": ["sales"], "tables": []},
                    "queue_name": "sync",
                },
            )
        ]

    async def test_reconcile_removes_the_old_global_schedule_and_that_of_a_removed_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Delete what no schema needs, and keep the schedule that matches."""
        calls = self.stub_dbos(
            monkeypatch,
            [
                self.schedule("sync"),
                self.schedule("sync-hr"),
                self.schedule("sync-gone"),
            ],
        )
        self.stub_config(monkeypatch, ["hr"])

        await workflow_body(workflows.reconcile_schedules)()

        assert calls == [("delete", "sync"), ("delete", "sync-gone")]

    async def test_reconcile_changes_nothing_when_the_schedules_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep the cron and the paused state that a user set."""
        calls = self.stub_dbos(
            monkeypatch,
            [self.schedule("sync-hr", schedule="1 1 * * *", status="PAUSED")],
        )
        self.stub_config(monkeypatch, ["hr"])

        await workflow_body(workflows.reconcile_schedules)()

        assert calls == []

    async def test_reconcile_fails_when_a_schedule_cannot_be_created(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real error is not a warning, so the workflow shows it."""
        self.stub_dbos(monkeypatch, [])
        self.stub_config(monkeypatch, ["hr"])

        async def refuse(**_: object) -> None:
            raise RuntimeError("Invalid cron schedule")

        monkeypatch.setattr(DBOS, "create_schedule_async", refuse)

        with pytest.raises(RuntimeError, match="Invalid cron schedule"):
            await workflow_body(workflows.reconcile_schedules)()

    def test_set_schedule_replaces_the_cron_and_keeps_the_rest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The new schedule has the same name, context and queue, and the new cron."""
        stored = self.schedule("sync-hr", cron_timezone="America/Sao_Paulo")
        calls = self.stub_dbos(monkeypatch, [stored])

        workflow_body(workflows.set_schedule)("sync-hr", "30 3 * * 1")

        assert calls == [
            ("delete", "sync-hr"),
            (
                "create",
                {
                    "schedule_name": "sync-hr",
                    "workflow_fn": workflows.run_sync,
                    "schedule": "30 3 * * 1",
                    "context": {"schemas": ["hr"], "tables": []},
                    "queue_name": "sync",
                    "cron_timezone": "America/Sao_Paulo",
                    "automatic_backfill": False,
                },
            ),
        ]

    def test_set_schedule_keeps_a_paused_schedule_paused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A new schedule starts active, so the workflow pauses it again."""
        calls = self.stub_dbos(monkeypatch, [self.schedule("sync-hr", status="PAUSED")])

        workflow_body(workflows.set_schedule)("sync-hr", "30 3 * * 1")

        assert [kind for kind, _ in calls] == ["delete", "create", "pause"]

    def test_set_schedule_puts_the_old_schedule_back_when_dbos_refuses_the_cron(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refused create must not leave the schema without a schedule, or without its pause."""
        calls = self.stub_dbos(
            monkeypatch,
            [self.schedule("sync-hr", status="PAUSED")],
            refused_cron="30 3 * * 1",
        )

        with pytest.raises(dbos_error.DBOSException, match="refused"):
            workflow_body(workflows.set_schedule)("sync-hr", "30 3 * * 1")

        assert [kind for kind, _ in calls] == ["delete", "create", "create", "pause"]
        assert [
            fields["schedule"] for _, fields in calls if isinstance(fields, dict)
        ] == ["30 3 * * 1", "0 2 * * *"]

    @pytest.mark.parametrize(
        ("name", "cron", "reason"),
        [
            pytest.param("sync-hr", "30 3 *", "Not a cron expression", id="bad-cron"),
            pytest.param(
                "sync-gone", "30 3 * * 1", "No such schedule", id="no-schedule"
            ),
        ],
    )
    def test_set_schedule_refuses_before_it_deletes(
        self, monkeypatch: pytest.MonkeyPatch, name: str, cron: str, reason: str
    ) -> None:
        """A refusal leaves the stored schedule alone, because the workflow checks first."""
        calls = self.stub_dbos(monkeypatch, [self.schedule("sync-hr")])

        with pytest.raises(ValueError, match=reason):
            workflow_body(workflows.set_schedule)(name, cron)

        assert calls == []


class TestRunProvision:
    """The provision Job, made from the CronJob template of the chart."""

    @staticmethod
    def stub_cluster(
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[AsyncMock, AsyncMock]:
        """Replace the Kubernetes calls of the step, and return both mocks."""

        @asynccontextmanager
        async def client() -> AsyncGenerator[str]:
            yield "client"

        create = AsyncMock(return_value="release-provision-ab12")
        wait = AsyncMock()
        monkeypatch.setattr(steps, "k8s_client", client)
        monkeypatch.setattr(steps, "create_job", create)
        monkeypatch.setattr(steps, "wait_for_job", wait)
        monkeypatch.setattr(settings, "PROVISION_CRONJOB", "release-provision")
        return create, wait

    async def test_creates_the_job_from_the_cronjob_and_waits_for_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The step makes one Job from the template, and waits for that Job."""
        create, wait = self.stub_cluster(monkeypatch)

        await workflow_body(steps.run_provision)()

        create.assert_awaited_once_with("client", "release-provision")
        wait.assert_awaited_once_with(
            "client", "release-provision-ab12", settings.PROVISION_TIMEOUT_SECONDS
        )

    async def test_does_nothing_without_the_name_of_the_cronjob(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment without the template, for example a local run, has nothing to call."""
        create, _ = self.stub_cluster(monkeypatch)
        monkeypatch.setattr(settings, "PROVISION_CRONJOB", None)

        await workflow_body(steps.run_provision)()

        create.assert_not_awaited()

    async def test_the_workflow_provisions_and_then_removes_stale_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """provision_schemas provisions, and then drops the S3 credentials of the schemas that left the console."""
        events: list[str] = []
        monkeypatch.setattr(
            workflows,
            "run_provision",
            AsyncMock(side_effect=lambda: events.append("provision")),
        )
        monkeypatch.setattr(
            workflows,
            "load_sync_config",
            AsyncMock(return_value=LoadedConfig(revision="r", document=DOCUMENT)),
        )

        async def cleanup(document: str) -> list[str]:
            events.append(document)
            return []

        monkeypatch.setattr(workflows, "cleanup_s3_credentials", cleanup)

        await workflow_body(workflows.provision_schemas)()

        assert events == ["provision", DOCUMENT]

    async def test_the_sync_workflow_provisions_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sync run starts with the same call, so that a lost event or a drift heals before the run plans."""
        _, events = stub_sync_run(monkeypatch, postgrest_reload_required=False)
        monkeypatch.setattr(
            workflows,
            "run_provision",
            AsyncMock(side_effect=lambda: events.append("provision")),
        )

        await run_sync()

        assert events[0] == "provision"


class TestRemoveDroppedTables:
    """The step removes what the console no longer lists, for each schema."""

    @staticmethod
    def stub(
        monkeypatch: pytest.MonkeyPatch, dropped: list[str]
    ) -> tuple[object, AsyncMock, AsyncMock]:
        """Replace the catalog, the bucket and the state with mocks."""
        connection = object()

        @asynccontextmanager
        async def duckdb_connect() -> AsyncGenerator[object]:
            yield connection

        @asynccontextmanager
        async def pg_connect(dsn: str) -> AsyncGenerator[object]:
            yield connection

        purge = AsyncMock()
        forget = AsyncMock()
        monkeypatch.setattr(DuckDB, "connect", duckdb_connect)
        monkeypatch.setattr(Postgres, "connect", pg_connect)
        monkeypatch.setattr(steps, "attach_catalog", AsyncMock())
        monkeypatch.setattr(
            steps, "drop_tables_except", AsyncMock(return_value=dropped)
        )
        monkeypatch.setattr(steps, "clear_s3_prefix", purge)
        monkeypatch.setattr(steps, "delete_removed_state", forget)
        return connection, purge, forget

    @pytest.mark.asyncio
    async def test_drops_purges_and_forgets_each_removed_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The folder of a dropped table goes, and the state keeps only the listed tables."""
        connection, purge, forget = self.stub(monkeypatch, ["old"])
        document = sync_config([FullTable(name="p.d.t")]).model_dump_json()

        removed = await workflow_body(steps.remove_dropped_tables)(document)

        assert removed == ["app.old"]
        purge.assert_awaited_once_with(table_prefix("app", "old"))
        forget.assert_awaited_once_with(connection, {"p.d.t"})

    @pytest.mark.asyncio
    async def test_purges_nothing_when_no_table_was_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A schema without removed tables leaves its bucket alone."""
        _, purge, _ = self.stub(monkeypatch, [])
        document = sync_config([FullTable(name="p.d.t")]).model_dump_json()

        assert await workflow_body(steps.remove_dropped_tables)(document) == []

        purge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_workflow_also_removes_the_credentials_of_removed_schemas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cleanup_tables gives the same loaded config to both steps."""
        monkeypatch.setattr(
            workflows,
            "load_sync_config",
            AsyncMock(return_value=LoadedConfig(revision="r", document=DOCUMENT)),
        )
        remove = AsyncMock(return_value=[])
        monkeypatch.setattr(workflows, "remove_dropped_tables", remove)
        cleanup = AsyncMock(return_value=[])
        monkeypatch.setattr(workflows, "cleanup_s3_credentials", cleanup)

        await workflow_body(workflows.cleanup_tables)()

        remove.assert_awaited_once_with(DOCUMENT)
        cleanup.assert_awaited_once_with(DOCUMENT)


class TestCleanupS3CredentialsStep:
    """The step removes the S3 identities of the schemas that the config no longer lists."""

    @pytest.mark.asyncio
    async def test_removes_the_identities_of_schemas_outside_the_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The config lists app, so every other schema loses its identity."""
        monkeypatch.setattr(settings, "S3_IDENTITIES", True)
        remove = AsyncMock(return_value=["gone"])
        monkeypatch.setattr(steps, "remove_stale_identities", remove)
        document = sync_config([FullTable(name="p.d.t")]).model_dump_json()

        removed = await workflow_body(steps.cleanup_s3_credentials)(document)

        assert removed == ["gone"]
        remove.assert_awaited_once_with({"app"})

    @pytest.mark.asyncio
    async def test_does_nothing_when_the_console_makes_no_identities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment without per-schema identities has no IAM API to call."""
        remove = AsyncMock()
        monkeypatch.setattr(steps, "remove_stale_identities", remove)
        monkeypatch.setattr(settings, "S3_IDENTITIES", False)
        document = sync_config([FullTable(name="p.d.t")]).model_dump_json()

        assert await workflow_body(steps.cleanup_s3_credentials)(document) == []

        remove.assert_not_awaited()


def test_dbos_can_send_logs_to_otlp() -> None:
    """DBOS imports this module when OTLP is on, and the sync image starts only with it."""
    importlib.import_module("opentelemetry.instrumentation.logging.handler")
