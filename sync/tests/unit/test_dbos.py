import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar, final
from unittest.mock import AsyncMock

import pytest
from psycopg import OperationalError
from pydantic import ValidationError

from sync.constants import POSTGREST_SELECTOR, PUBLISH_QUEUE
from sync.dbos import main as dbos_main
from sync.dbos import steps, utils, workflows
from sync.duckdb import DuckDB
from sync.ducklake import DuckLakePaths
from sync.models import (
    FullTable,
    LoadedConfig,
    SyncPlan,
    SyncScope,
    SyncWork,
)
from sync.postgres import Postgres
from sync.settings import settings
from sync.types import KubernetesClient
from tests.fixtures.types import Calls, FakeKubernetes
from tests.helpers import (
    DOCUMENT,
    catalog_commit_error,
    dump_task,
    publish,
    run_sync,
    stub_sync_run,
    sync_config,
    workflow_body,
)


class TestTransientRetryClassification:
    """TransientRetryClassification behavior tests."""

    def test_classifies_runtime_errors_as_transient(self) -> None:
        """Classify runtime errors as transient."""
        assert utils.retry_transient(RuntimeError("temporary"))
        assert not utils.retry_transient(ValueError("invalid"))

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


class TestRunSyncRestartsPostgrest:
    """run_sync restarts PostgREST once, only when the view set changed."""

    @pytest.mark.parametrize(
        ("postgrest_restart_required", "snapshot_id", "expected"),
        [
            pytest.param(False, None, [], id="nothing-changed"),
            pytest.param(False, 7, [], id="snapshot-published-without-view-change"),
            pytest.param(
                True, None, ["restart_postgrest"], id="view-change-without-snapshot"
            ),
            pytest.param(True, 7, ["restart_postgrest"], id="snapshot-and-view-change"),
        ],
    )
    @pytest.mark.asyncio
    async def test_restarts_postgrest_only_after_a_view_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        postgrest_restart_required: bool,
        snapshot_id: int | None,
        expected: list[str],
    ) -> None:
        """Restarts postgrest only after a view change."""
        calls, events = stub_sync_run(
            monkeypatch,
            postgrest_restart_required=postgrest_restart_required,
            snapshot_id=snapshot_id,
        )

        await run_sync()

        assert calls == expected
        assert events == ["enqueue:app", "wait:app"]

    @pytest.mark.asyncio
    async def test_waits_for_every_schema_publisher_before_it_restarts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wait for both publishers, then restart once."""
        calls, events = stub_sync_run(
            monkeypatch,
            postgrest_restart_required=True,
            snapshot_ids={"one": 3, "two": None},
            plans=[SyncPlan(schema_name="one"), SyncPlan(schema_name="two")],
        )

        await run_sync()

        assert calls == ["restart_postgrest"]
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
            postgrest_restart_required=False,
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


class TestLoadSyncConfig:
    """load_sync_config step behavior tests."""

    @pytest.fixture
    def active(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """Stand in for the database and return the reader of the active revision."""

        @asynccontextmanager
        async def connect(dsn: str) -> AsyncGenerator[None]:
            yield

        read = AsyncMock(return_value=None)
        monkeypatch.setattr(Postgres, "connect", connect)
        monkeypatch.setattr(steps, "read_active_config", read)
        return read

    async def test_takes_the_active_revision_from_the_database(
        self, active: AsyncMock
    ) -> None:
        """Use the document and the id of the active revision."""
        active.return_value = LoadedConfig(revision="7", document=DOCUMENT)

        loaded = await workflow_body(steps.load_sync_config)()

        assert loaded == LoadedConfig(revision="7", document=DOCUMENT)

    @pytest.mark.usefixtures("active")
    async def test_reads_the_file_while_no_revision_is_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fall back to the file, which has no revision."""
        path = tmp_path / "sync.json"
        path.write_text(DOCUMENT)
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", path)

        loaded = await workflow_body(steps.load_sync_config)()

        assert loaded == LoadedConfig(revision="file", document=DOCUMENT)

    async def test_rejects_a_document_that_the_engine_cannot_use(
        self, active: AsyncMock
    ) -> None:
        """Fail the run at its start, before any work."""
        active.return_value = LoadedConfig(
            revision="8", document='{"schemas": {"app": 1}}'
        )

        with pytest.raises(ValidationError):
            await workflow_body(steps.load_sync_config)()

    async def test_fails_when_the_database_fails(self, active: AsyncMock) -> None:
        """Raise, so that DBOS retries the step."""
        active.side_effect = OperationalError("down")

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
            postgrest_restart_required=False,
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
    async def test_plans_only_the_scope_of_an_on_demand_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hand the scope of the request to planning, and the whole document to seeding."""
        plan = SyncPlan(schema_name="app")
        stub_sync_run(monkeypatch, postgrest_restart_required=False, snapshot_id=None)
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
            postgrest_restart_required=False,
            snapshot_ids={"one": None, "two": None},
            plans=[SyncPlan(schema_name="one"), SyncPlan(schema_name="two")],
            publishes=publishes,
        )

        await run_sync()

        assert publishes == [(PUBLISH_QUEUE, "one"), (PUBLISH_QUEUE, "two")]


@final
class FakeConnection:
    """Async context manager that yields a fixed client, like `AsyncClient(namespace=...)`."""

    def __init__(self, client: KubernetesClient) -> None:
        self.client = client

    async def __aenter__(self) -> KubernetesClient:
        return self.client

    async def __aexit__(self, *_: object) -> None:
        return None


def open_client(monkeypatch: pytest.MonkeyPatch, client: FakeKubernetes) -> list[str]:
    """Make the steps open the given client, and return the namespaces they open it for."""
    namespaces: list[str] = []

    def factory(namespace: str) -> FakeConnection:
        namespaces.append(namespace)
        return FakeConnection(client)

    monkeypatch.setattr(steps, "AsyncClient", factory)
    return namespaces


class TestRestartPostgrestStep:
    """The step restarts every PostgREST Deployment together with one client."""

    @pytest.mark.asyncio
    async def test_restarts_the_deployments_that_the_selector_finds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Restarts the deployments that the selector finds."""
        client = FakeKubernetes()
        namespaces = open_client(monkeypatch, client)
        namespace = settings.KUBERNETES_NAMESPACE
        found = AsyncMock(return_value=["postgrest", "postgrest-ro"])
        restart = AsyncMock()
        monkeypatch.setattr(steps, "list_deployments", found)
        monkeypatch.setattr(steps, "restart_deployment", restart)

        await workflow_body(steps.restart_postgrest)()

        assert namespaces == [namespace]
        found.assert_awaited_once_with(client, namespace, POSTGREST_SELECTOR)
        assert [call.args[:3] for call in restart.await_args_list] == [
            (client, namespace, "postgrest"),
            (client, namespace, "postgrest-ro"),
        ]
        assert len({call.args[3] for call in restart.await_args_list}) == 1

    @pytest.mark.asyncio
    async def test_fails_when_the_selector_matches_no_deployment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fails when the selector matches no deployment."""
        open_client(monkeypatch, FakeKubernetes())
        monkeypatch.setattr(steps, "list_deployments", AsyncMock(return_value=[]))

        with pytest.raises(RuntimeError, match="No PostgREST Deployment found"):
            await workflow_body(steps.restart_postgrest)()


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


class TestPublishSchemaOrder:
    """publish_schema workflow behavior tests."""

    async def test_commits_state_after_the_snapshot_and_returns_it(
        self, calls: Calls
    ) -> None:
        """Commits state after the snapshot and returns it."""
        snapshot_id = await publish(SyncPlan(schema_name="app"))

        assert calls.names == [
            "commit_ducklake_snapshot",
            "record_publish_metrics",
            "commit_table_state",
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
    applied: ClassVar[list[str]] = []
    existing: ClassVar[object | None] = None

    def __init__(self, **_: object) -> None:
        """Accept the configuration."""

    @classmethod
    def listen_queues(cls, queues: list[str]) -> None:
        """Ignore the queues to listen to."""

    @classmethod
    def launch(cls) -> None:
        """Ignore the launch."""

    @classmethod
    async def register_queue_async(
        cls, name: str, *, on_conflict: str = "always_update", **_: object
    ) -> None:
        """Record the queue and how it treats an existing one."""
        cls.queues.append((name, on_conflict))

    @classmethod
    async def get_schedule_async(cls, _name: str) -> object | None:
        """Answer with the schedule that the test prepared, if any."""
        return cls.existing

    @classmethod
    async def apply_schedules_async(cls, schedules: list[dict[str, object]]) -> None:
        """Record the names of the applied schedules."""
        cls.applied.extend(str(entry["schedule_name"]) for entry in schedules)


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
        RecordingDbos.applied = []
        RecordingDbos.existing = None
        monkeypatch.setattr(dbos_main, "DBOS", RecordingDbos)
        monkeypatch.setattr(dbos_main, "Event", ReturnsAtOnce)

    @pytest.mark.asyncio
    async def test_leaves_the_stored_queues_alone(self) -> None:
        """Seed each queue once, so a limit that the console changed survives a restart."""
        await dbos_main.serve()

        assert RecordingDbos.queues == [
            ("sync", "never_update"),
            ("dump", "never_update"),
            ("publish", "never_update"),
            ("validate", "never_update"),
        ]

    @pytest.mark.asyncio
    async def test_creates_the_schedule_when_it_is_missing(self) -> None:
        """Create the nightly schedule on the first start."""
        await dbos_main.serve()

        assert RecordingDbos.applied == ["sync"]

    @pytest.mark.asyncio
    async def test_leaves_the_stored_schedule_alone(self) -> None:
        """Keep a schedule that exists, because the console may have changed it."""
        RecordingDbos.existing = object()

        await dbos_main.serve()

        assert RecordingDbos.applied == []


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
