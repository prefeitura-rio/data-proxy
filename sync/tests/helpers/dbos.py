"""Stub the workflows, the steps, the logs and the clients of the sync worker."""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from inspect import unwrap
from logging import LogRecord
from types import SimpleNamespace
from unittest.mock import AsyncMock

import duckdb
import psycopg
import pytest
from httpx2 import AsyncClient, MockTransport, Request, Response
from lightkube.models.batch_v1 import CronJobSpec, JobSpec, JobTemplateSpec
from lightkube.models.core_v1 import Container, PodSpec, PodTemplateSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.batch_v1 import CronJob
from psycopg.rows import TupleRow

from sync.dbos import logs, workflows
from sync.models import (
    DumpResult,
    DumpTask,
    LoadedConfig,
    SyncPlan,
    SyncWork,
)
from tests.fixtures.types import RecordedOptions
from tests.helpers.state import DOCUMENT


def workflow_body[**P, R](workflow: Callable[P, R]) -> Callable[P, R]:
    """Return the body of a DBOS workflow so it runs without the DBOS runtime."""
    body: Callable[P, R] = unwrap(workflow)
    return body


async def publish(plan: SyncPlan) -> int | None:
    """Run the publish workflow body for one plan with no failed paths."""
    return await workflow_body(workflows.publish_schema)("run", plan, set(), DOCUMENT)


def enqueue_label(workflow: object, args: tuple[object, ...]) -> str:
    """Name what an enqueued workflow works on: a dump task's table or a plan's schema."""
    if workflow is workflows.dump_task:
        task = args[0]
        if not isinstance(task, DumpTask):
            raise TypeError("Dump workflow requires a dump task")
        return task.table
    plan = args[1]
    if not isinstance(plan, SyncPlan):
        raise TypeError("Publish workflow requires a sync plan")
    return plan.schema_name


def stub_sync_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    postgrest_reload_required: bool,
    snapshot_id: int | None = None,
    snapshot_ids: dict[str, int | None] | None = None,
    plans: list[SyncPlan] | None = None,
    tasks: list[DumpTask] | None = None,
    publishes: list[tuple[str, object]] | None = None,
) -> tuple[list[str], list[str]]:
    """Stub run_sync steps and return the serving calls and the queue events."""
    calls: list[str] = []
    events: list[str] = []

    async def restart() -> None:
        calls.append("reload_postgrest")

    async def enqueue_workflow(queue: str, workflow: object, *args: object) -> object:
        label = enqueue_label(workflow, args)
        is_dump = workflow is workflows.dump_task
        result: DumpResult | int | None = DumpResult()
        if not is_dump:
            result = snapshot_ids[label] if snapshot_ids is not None else snapshot_id
            if publishes is not None:
                publishes.append(
                    (queue, RecordedOptions.active.get("queue_partition_key"))
                )
        events.append(f"enqueue:{label}")

        async def get_result() -> DumpResult | int | None:
            events.append(f"wait:{label}")
            return result

        return SimpleNamespace(get_result=get_result, workflow_id=f"workflow:{label}")

    monkeypatch.setattr(
        workflows,
        "DBOS",
        SimpleNamespace(
            workflow_id="run",
            enqueue_workflow_async=AsyncMock(side_effect=enqueue_workflow),
        ),
    )
    monkeypatch.setattr(workflows, "SetEnqueueOptions", RecordedOptions)
    monkeypatch.setattr(
        workflows,
        "load_sync_config",
        AsyncMock(return_value=LoadedConfig(revision="3", document=DOCUMENT)),
    )
    monkeypatch.setattr(
        workflows,
        "build_sync_work",
        AsyncMock(
            return_value=SyncWork(
                plans=plans
                if plans is not None and len(plans) > 0
                else [SyncPlan(schema_name="app")],
                tasks=[] if tasks is None else tasks,
            )
        ),
    )
    monkeypatch.setattr(
        workflows,
        "seed_schemas",
        AsyncMock(return_value=postgrest_reload_required),
    )
    monkeypatch.setattr(
        workflows, "reconcile_idle_schemas", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(workflows, "record_scope", AsyncMock())
    monkeypatch.setattr(workflows, "run_provision", AsyncMock())
    monkeypatch.setattr(workflows, "reload_postgrest", restart)
    monkeypatch.setattr(workflows, "finalize_run", AsyncMock())
    return calls, events


async def run_sync(context: dict[str, list[str]] | None = None) -> None:
    """Run the sync workflow body."""
    await workflow_body(workflows.run_sync)(datetime.now(UTC), context)


def catalog_commit_error(reason: str) -> duckdb.TransactionException:
    """Build the error that a failed DuckLake commit raises."""
    return duckdb.TransactionException(
        "\n".join(
            (
                "TransactionContext Error: Failed to commit: Failed to commit DuckLake transaction.",
                f"Failed to flush changes into DuckLake: {reason}",
            )
        )
    )


def answers_for(
    connection: psycopg.Connection[TupleRow], role: str, features: list[str]
) -> dict[str, bool]:
    """Ask rbac.can about every feature as a token with one role."""
    connection.execute("SELECT set_config('app.claim_role', %s, true)", (role,))
    connection.execute(b"SELECT rbac.load()")
    rows = connection.execute(
        "SELECT feature, rbac.can(feature) FROM unnest(%s::text[]) AS feature",
        (features,),
    ).fetchall()

    return {str(feature): bool(answer) for feature, answer in rows}


def client_answering(statuses: dict[str, int], seen: list[Request]) -> AsyncClient:
    """Build a client whose pods answer with the given status, by address."""

    def answer(request: Request) -> Response:
        seen.append(request)
        return Response(statuses.get(request.url.host, 200), json={"changed": True})

    return AsyncClient(transport=MockTransport(answer))


def workflow(monkeypatch: pytest.MonkeyPatch, workflow_id: str | None) -> AsyncMock:
    """Run the log functions in a workflow, or outside one, and return the stream writer."""
    write_stream = AsyncMock()
    monkeypatch.setattr(
        logs,
        "DBOS",
        SimpleNamespace(workflow_id=workflow_id, write_stream_async=write_stream),
    )
    return write_stream


def written(stream: AsyncMock) -> tuple[str, dict[str, object]]:
    """Return the key and the object of the one write to the stream."""
    call = stream.await_args
    assert call is not None
    key, entry = call.args

    return key, entry


async def silent(message: str, /, **fields: str | float | bool) -> None:
    """Ignore a report."""


def cronjob(
    *,
    metadata: ObjectMeta | None = None,
    template_metadata: ObjectMeta | None = None,
) -> CronJob:
    """Build a CronJob with one container in its job template."""
    return CronJob(
        metadata=ObjectMeta(name="provision", uid="uid-1")
        if metadata is None
        else metadata,
        spec=CronJobSpec(
            schedule="0 0 31 2 *",
            jobTemplate=JobTemplateSpec(
                metadata=template_metadata,
                spec=JobSpec(
                    template=PodTemplateSpec(
                        spec=PodSpec(containers=[Container(name="run", image="img:1")])
                    )
                ),
            ),
        ),
    )


def raised_in_project_file(filename: str, line: int) -> BaseException:
    """Raise an error from code that Python takes for a file at the given path."""
    code = compile("\n" * (line - 1) + "raise ValueError('boom')", filename, "exec")

    try:
        exec(code, {})  # ruff: ignore[exec-builtin]
    except ValueError as error:
        return error

    raise AssertionError("The code did not raise")


def record(
    level: int, message: str, *, error: BaseException | None = None
) -> LogRecord:
    """Build a log record like the logger of DBOS makes."""
    exc_info = None if error is None else (type(error), error, error.__traceback__)

    return LogRecord("dbos", level, "file.py", 1, message, (), exc_info)


class FakeRedis:
    """A Redis client that records when it flushes a database."""

    def __init__(self, db: int | None, events: list[str]) -> None:
        """Keep the database that the client opened and the list that records the calls."""
        self.db = db
        self.events = events

    async def flushdb(self) -> None:
        """Record that the client flushed the database."""
        self.events.append(f"flush:{self.db}")


def stub_cache_redis(monkeypatch: pytest.MonkeyPatch, cache_db: int) -> list[str]:
    """Put a fake Redis in the settings of sync.cache and return the list of its calls."""
    events: list[str] = []

    @asynccontextmanager
    async def redis(db: int | None = None) -> AsyncGenerator[FakeRedis]:
        events.append(f"open:{db}")
        try:
            yield FakeRedis(db, events)
        finally:
            events.append(f"close:{db}")

    monkeypatch.setattr(
        "sync.cache.settings",
        SimpleNamespace(PROXY_CACHE_REDIS_DB=cache_db, redis=redis),
    )
    return events
