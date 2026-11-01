"""Tests for the workflows that control schedules, provision schemas and clean up."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from dbos import DBOS
from dbos import error as dbos_error

from sync.dbos import steps, workflows
from sync.duckdb import DuckDB
from sync.ducklake import table_prefix
from sync.models import (
    FullTable,
    LoadedConfig,
    SchemaConfig,
    SyncConfig,
)
from sync.postgres import Postgres
from sync.settings import settings
from tests.helpers.dbos import (
    run_sync,
    stub_sync_run,
    workflow_body,
)
from tests.helpers.state import DOCUMENT, sync_config

CONNECTION = object()


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
