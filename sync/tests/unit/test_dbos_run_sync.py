"""Tests for the run_sync workflow."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from psycopg import OperationalError
from pydantic import ValidationError

from sync.constants import PUBLISH_QUEUE
from sync.dbos import steps, workflows
from sync.models import (
    DumpResult,
    DumpStatus,
    LoadedConfig,
    SyncConfig,
    SyncPlan,
    SyncScope,
    SyncWork,
)
from sync.postgres import Postgres
from sync.state import ScopeRow
from sync.utils import sha256_hex
from tests.helpers.dbos import (
    enqueue_label,
    run_sync,
    stub_sync_run,
    workflow_body,
)
from tests.helpers.state import DOCUMENT, dump_task


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
    async def test_gives_the_cached_answers_of_the_planned_schemas_a_new_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the schemas of the plans change, so the cache of another schema survives the run."""
        stub_sync_run(
            monkeypatch,
            postgrest_reload_required=False,
            snapshot_id=None,
            plans=[SyncPlan(schema_name="two"), SyncPlan(schema_name="one")],
        )
        finalize = AsyncMock()
        monkeypatch.setattr(workflows, "finalize_run", finalize)

        await run_sync()

        finalize.assert_awaited_once_with(["one", "two"])

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
