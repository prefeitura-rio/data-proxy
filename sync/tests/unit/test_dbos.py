import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
from psycopg import OperationalError
from pydantic import ValidationError

from sync.constants import PUBLISH_QUEUE
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
from sync.utils import sha256_hex
from tests.fixtures.types import Calls
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

        assert connections == [settings.PG_DATABASE_URL]
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
            ("proxy", "never_update"),
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

    async def test_the_workflow_runs_the_step(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reload_proxy is one step."""
        notify = AsyncMock()
        monkeypatch.setattr(workflows, "notify_proxies", notify)

        await workflow_body(workflows.reload_proxy)()

        notify.assert_awaited_once_with()
