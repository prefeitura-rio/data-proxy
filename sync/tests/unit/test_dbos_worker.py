"""Tests for the DBOS worker: its workflows, retries, queues, startup and logs."""

import asyncio
import importlib
import inspect

import pytest
from psycopg import OperationalError

from sync.dbos import main as dbos_main
from sync.dbos import utils, workflows
from sync.settings import settings
from tests.fixtures.types import (
    RecordedOptions,
    RecordingDbos,
    ReturnsAtOnce,
)
from tests.helpers.dbos import (
    catalog_commit_error,
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
        assert utils.retry_catalog_locked(error) is expected


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


def test_dbos_can_send_logs_to_otlp() -> None:
    """DBOS imports this module when OTLP is on, and the sync image starts only with it."""
    importlib.import_module("opentelemetry.instrumentation.logging.handler")
