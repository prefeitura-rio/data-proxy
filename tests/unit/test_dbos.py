"""Unit tests for DBOS utility behavior."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace, TracebackType
from typing import final
from unittest.mock import AsyncMock, MagicMock

import pytest

from data_proxy.constants import POOLER_SELECTOR, POSTGREST_SELECTOR
from data_proxy.dbos import steps, utils, workflows
from data_proxy.duckdb import DuckDB
from data_proxy.ducklake import DuckLakePaths
from data_proxy.models import FullTable, ServingDeployments, SyncPlan
from data_proxy.postgres import Postgres
from data_proxy.settings import settings
from data_proxy.types import KubernetesClient
from tests.fixtures.types import Calls, FakeKubernetes
from tests.helpers import (
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
        assert utils.retry_catalog_locked(error) is expected


SERVING_REFRESH = [
    "refresh:[('app', 7)]",
    "restart:PgBouncer:['pooler']",
    "restart:PostgREST:['postgrest']",
]


class TestRunSyncRefreshesServing:
    """run_sync refreshes serving once, after every schema publisher returns."""

    @pytest.mark.parametrize(
        ("postgrest_restart_required", "snapshot_id", "expected"),
        [
            pytest.param(False, None, [], id="nothing-changed"),
            pytest.param(
                False, 7, SERVING_REFRESH, id="snapshot-published-without-view-change"
            ),
            pytest.param(
                True,
                None,
                ["restart:PostgREST:['postgrest']"],
                id="view-change-without-published-snapshot",
            ),
            pytest.param(True, 7, SERVING_REFRESH, id="snapshot-and-view-change"),
        ],
    )
    @pytest.mark.asyncio
    async def test_refreshes_and_restarts_only_what_changed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        postgrest_restart_required: bool,
        snapshot_id: int | None,
        expected: list[str],
    ) -> None:
        calls, events = stub_sync_run(
            monkeypatch,
            postgrest_restart_required=postgrest_restart_required,
            snapshot_id=snapshot_id,
        )

        await run_sync()

        assert calls == expected
        assert events == ["enqueue:app", "wait:app"]

    @pytest.mark.asyncio
    async def test_refreshes_only_the_schemas_that_published(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Skip the schema without a snapshot and refresh the other once."""
        calls, events = stub_sync_run(
            monkeypatch,
            postgrest_restart_required=False,
            snapshot_id=3,
            plans=[SyncPlan(schema_name="one"), SyncPlan(schema_name="two")],
        )

        await run_sync()

        assert calls[0] == "refresh:[('one', 3), ('two', 3)]"
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


class TestDetectPublishedSchemas:
    """Only schemas with a new snapshot count as published."""

    @pytest.mark.asyncio
    async def test_keeps_the_schemas_with_a_snapshot(self) -> None:
        detect = workflow_body(steps.detect_published_schemas)

        assert await detect({"test": 22, "other": None, "app": 3}) == {
            "app": 3,
            "test": 22,
        }

    @pytest.mark.asyncio
    async def test_returns_nothing_when_no_schema_published(self) -> None:
        detect = workflow_body(steps.detect_published_schemas)

        assert await detect({"test": None}) == {}


class TestRefreshCatalogsWorkflow:
    """The refresh workflow runs one child per schema and instance until the primary catches up."""

    @staticmethod
    def stub(
        monkeypatch: pytest.MonkeyPatch, lagging: list[dict[str, int]]
    ) -> list[str]:
        """Stub the workflow steps and return the events they record."""
        events: list[str] = []
        remaining = iter(lagging)

        async def enqueue(queue: str, _workflow: object, *args: object) -> object:
            events.append(f"enqueue:{queue}:{args[0]}:{args[1]}")

            async def get_result() -> str:
                events.append(f"wait:{args[0]}:{args[1]}")
                return "job"

            return SimpleNamespace(get_result=get_result)

        async def sleep(seconds: float) -> None:
            events.append(f"sleep:{seconds:g}")

        async def check(snapshots: dict[str, int]) -> dict[str, int]:
            events.append(f"check:{sorted(snapshots)}")
            return next(remaining)

        monkeypatch.setattr(
            workflows,
            "DBOS",
            SimpleNamespace(enqueue_workflow_async=enqueue, sleep_async=sleep),
        )
        monkeypatch.setattr(
            workflows,
            "list_instance_claims",
            AsyncMock(return_value={"pg-1": "claim-1", "pg-2": "claim-2"}),
        )
        monkeypatch.setattr(workflows, "find_lagging_snapshots", check)
        monkeypatch.setattr(settings, "READER_REFRESH_ATTEMPTS", 3)
        monkeypatch.setattr(settings, "READER_REFRESH_RETRY_SECONDS", 5.0)

        return events

    @pytest.mark.asyncio
    async def test_enqueues_every_schema_instance_pair_before_the_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events = self.stub(monkeypatch, [{}])

        await workflow_body(workflows.refresh_catalogs)({"one": 1, "two": 2})

        assert events[:4] == [
            "enqueue:refresh:one:pg-1",
            "enqueue:refresh:one:pg-2",
            "enqueue:refresh:two:pg-1",
            "enqueue:refresh:two:pg-2",
        ]
        assert events[-1] == "check:['one', 'two']"
        assert len([event for event in events if event.startswith("wait:")]) == 4
        assert not [event for event in events if event.startswith("sleep:")]

    @pytest.mark.asyncio
    async def test_runs_again_only_for_the_schemas_that_still_lag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events = self.stub(monkeypatch, [{"two": 2}, {}])

        await workflow_body(workflows.refresh_catalogs)({"one": 1, "two": 2})

        second_round = events[events.index("sleep:5") + 1 :]
        assert [e for e in second_round if e.startswith("enqueue:")] == [
            "enqueue:refresh:two:pg-1",
            "enqueue:refresh:two:pg-2",
        ]
        assert second_round[-1] == "check:['two']"

    @pytest.mark.asyncio
    async def test_fails_when_the_primary_never_reports_the_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events = self.stub(monkeypatch, [{"one": 1}] * 3)

        with pytest.raises(RuntimeError, match=r"after 3 attempts: \['one'\]"):
            await workflow_body(workflows.refresh_catalogs)({"one": 1})

        assert events.count("sleep:5") == 2


@final
class FakeConnection:
    """Async context manager that yields a fixed client, like `AsyncClient(namespace=...)`."""

    def __init__(self, client: KubernetesClient) -> None:
        self.client = client

    async def __aenter__(self) -> KubernetesClient:
        return self.client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def open_client(monkeypatch: pytest.MonkeyPatch, client: FakeKubernetes) -> list[str]:
    """Make the steps open the given client, and return the namespaces they open it for."""
    namespaces: list[str] = []

    def factory(namespace: str) -> FakeConnection:
        namespaces.append(namespace)
        return FakeConnection(client)

    monkeypatch.setattr(steps, "AsyncClient", factory)
    return namespaces


class TestServingSteps:
    """The serving steps open one client each and pass the right labels and names."""

    @pytest.mark.asyncio
    async def test_lists_poolers_and_postgrest_by_their_selectors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = FakeKubernetes()
        namespaces = open_client(monkeypatch, client)
        clients: list[KubernetesClient] = []

        async def list_by_selector(
            received: KubernetesClient, _namespace: str, labels: dict[str, str]
        ) -> list[str]:
            clients.append(received)
            if labels == POOLER_SELECTOR:
                return ["pooler", "pooler-ro"]
            if labels == POSTGREST_SELECTOR:
                return ["postgrest", "postgrest-ro"]
            return []

        monkeypatch.setattr(steps, "list_deployments", list_by_selector)

        deployments = await workflow_body(steps.list_serving_deployments)()

        assert deployments == ServingDeployments(
            poolers=["pooler", "pooler-ro"], postgrest=["postgrest", "postgrest-ro"]
        )
        assert namespaces == [settings.KUBERNETES_NAMESPACE]
        assert clients == [client, client]

    @pytest.mark.asyncio
    async def test_starts_both_lookups_before_either_finishes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        open_client(monkeypatch, FakeKubernetes())
        events: list[str] = []

        async def list_by_selector(
            _client: KubernetesClient, _namespace: str, labels: dict[str, str]
        ) -> list[str]:
            kind = "pooler" if labels == POOLER_SELECTOR else "postgrest"
            events.append(f"start:{kind}")
            await asyncio.sleep(0)
            events.append(f"end:{kind}")
            return [kind]

        monkeypatch.setattr(steps, "list_deployments", list_by_selector)

        await workflow_body(steps.list_serving_deployments)()

        assert events == [
            "start:pooler",
            "start:postgrest",
            "end:pooler",
            "end:postgrest",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("poolers", "postgrest", "kind"),
        [
            pytest.param([], ["postgrest"], "PgBouncer", id="no-pooler"),
            pytest.param(["pooler"], [], "PostgREST", id="no-postgrest"),
        ],
    )
    async def test_fails_when_a_selector_matches_no_deployment(
        self,
        monkeypatch: pytest.MonkeyPatch,
        poolers: list[str],
        postgrest: list[str],
        kind: str,
    ) -> None:
        open_client(monkeypatch, FakeKubernetes())

        async def list_by_selector(
            _client: KubernetesClient, _namespace: str, labels: dict[str, str]
        ) -> list[str]:
            return poolers if labels == POOLER_SELECTOR else postgrest

        monkeypatch.setattr(steps, "list_deployments", list_by_selector)

        with pytest.raises(RuntimeError, match=kind):
            await workflow_body(steps.list_serving_deployments)()

    @pytest.mark.asyncio
    async def test_opens_a_client_for_the_namespace_and_passes_it_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = FakeKubernetes()
        namespaces = open_client(monkeypatch, client)
        namespace = settings.KUBERNETES_NAMESPACE
        list_claims = AsyncMock(return_value={"pg-1": "claim-1"})
        run = AsyncMock(return_value="job")
        restart = AsyncMock()
        monkeypatch.setattr(steps, "list_catalog_claims", list_claims)
        monkeypatch.setattr(steps, "run_job", run)
        monkeypatch.setattr(steps, "restart_deployment", restart)

        claims = await workflow_body(steps.list_instance_claims)()
        job = await workflow_body(steps.run_refresh_job)("test", "pg-1", "claim-1")
        await workflow_body(steps.restart_serving)("PgBouncer", ["a", "b"])

        assert claims == {"pg-1": "claim-1"}
        assert job == "job"
        assert namespaces == [namespace] * 3
        list_claims.assert_awaited_once_with(client, namespace)
        run.assert_awaited_once_with(client, namespace, "test", "pg-1", "claim-1")
        assert [call.args[:3] for call in restart.await_args_list] == [
            (client, namespace, "a"),
            (client, namespace, "b"),
        ]
        assert len({call.args[3] for call in restart.await_args_list}) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("reported", "expected"),
        [
            pytest.param({"one": 4, "two": 5}, {}, id="primary-at-the-snapshots"),
            pytest.param({"one": 9, "two": 6}, {}, id="primary-ahead"),
            pytest.param({"one": 3, "two": 5}, {"one": 4}, id="one-behind"),
            pytest.param({"one": None, "two": 5}, {"one": 4}, id="catalog-missing"),
        ],
    )
    async def test_returns_the_snapshots_that_the_primary_does_not_report(
        self,
        monkeypatch: pytest.MonkeyPatch,
        reported: dict[str, int | None],
        expected: dict[str, int],
    ) -> None:
        async def reader_snapshot(_conn: object, schema_name: str) -> int | None:
            return reported[schema_name]

        monkeypatch.setattr(steps, "reader_snapshot", reader_snapshot)
        monkeypatch.setattr(Postgres, "connect", MagicMock())

        lagging = await workflow_body(steps.find_lagging_snapshots)(
            {"two": 5, "one": 4}
        )

        assert lagging == expected


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

        monkeypatch.setitem(
            settings.__dict__, "sync_config", sync_config([FullTable(name="p.d.t")])
        )
        monkeypatch.setattr(DuckDB, "connect", connect)
        monkeypatch.setattr(steps, "apply_maintenance", maintain)

        snapshot_id = await workflow_body(steps.apply_ducklake_maintenance)("app")

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
