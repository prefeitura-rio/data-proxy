"""A sync run with its real steps, a fake source and the real PostgreSQL, S3 and DuckLake of the test stack."""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import final
from unittest.mock import AsyncMock

import psycopg
import pytest
from psycopg.sql import SQL, Identifier
from pydantic import JsonValue

from sync import extraction
from sync.dbos import steps, workflows
from sync.duckdb import DuckDB
from sync.models import (
    DumpTask,
    FullTable,
    LoadedConfig,
    SchemaConfig,
    SourceConfig,
    SyncConfig,
)
from sync.settings import settings
from sync.sources import registry
from sync.views import stages
from tests.constants import S3_ACCESS_KEY, S3_ENDPOINT, S3_SECRET_KEY
from tests.fixtures.types import Postgres
from tests.helpers import RecordedOptions, initialize_schemas, workflow_body


@final
@dataclass(frozen=True, slots=True)
class RangeSource:
    """A source of 20 rows. A table named broken fails when DuckDB reads it, and not when it plans."""

    name: str = "range"
    fallback: None = None
    load: str = ""
    extensions: tuple[str, ...] = ()

    def with_settings(self, settings: dict[str, JsonValue] | None) -> RangeSource:
        """Build a settings-free test source."""
        return RangeSource()

    def validate(self, table: str) -> None:
        """Accept every test table reference."""

    def scan(self, table: str) -> SQL:
        """Return the rows of the table as a DuckDB expression."""
        if table.endswith(".broken"):
            return SQL(
                "(SELECT i AS cpf, 'name_' || i AS name FROM range(20) AS t(i) WHERE error('extraction exploded'))"
            )

        return SQL("(SELECT i AS cpf, 'name_' || i AS name FROM range(20) AS t(i))")

    async def modified(self, table: str) -> str:
        """Return a value that never changes, so a second run finds nothing to do."""
        return "modified"

    async def close(self) -> None:
        """Release no resources."""


# The steps that the run performs for real. The others reach Kubernetes, Redis or PostgREST.
REAL_STEPS = (
    "build_sync_work",
    "record_scope",
    "seed_schemas",
    "record_seed_metrics",
    "reconcile_idle_schemas",
    "extract_task",
    "record_dump_failure",
    "record_dump_metrics",
    "commit_ducklake_snapshot",
    "record_publish_metrics",
    "apply_ducklake_maintenance",
)


@dataclass
class Pipeline:
    """One schema with a table that dumps well and a table that fails, and the run over them."""

    pg: Postgres
    schema: str
    config: SyncConfig
    reloads: AsyncMock
    load: AsyncMock
    extractions: list[str] = field(default_factory=list)

    @property
    def good(self) -> str:
        """The table that dumps well."""
        return f"p.{self.schema}.people"

    @property
    def broken(self) -> str:
        """The table that fails when DuckDB reads it."""
        return f"p.{self.schema}.broken"

    def only_the_good_table(self) -> None:
        """Take the table that fails out of the configuration of the next runs."""
        schema = self.config.schemas[self.schema]
        good = SyncConfig(
            schemas={
                self.schema: schema.model_copy(
                    update={"tables": [t for t in schema.tables if t.name == self.good]}
                )
            }
        )
        self.load.return_value = LoadedConfig(
            revision="2", document=good.model_dump_json()
        )

    async def run(self, context: dict[str, list[str]] | None = None) -> None:
        """Run the sync workflow body once."""
        await workflow_body(workflows.run_sync)(datetime.now(UTC), context)


@pytest.fixture
async def pipeline(
    postgres: Postgres, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Pipeline]:
    """Wire run_sync to its real steps, and clean the data that the run leaves behind."""
    schema = postgres.namespace.schema
    monkeypatch.setattr(registry.sources, "sources", {"range": RangeSource()})
    config = SyncConfig(
        schemas={
            schema: SchemaConfig(
                source=SourceConfig(type="range"),
                tables=[
                    FullTable(name=f"p.{schema}.people"),
                    FullTable(name=f"p.{schema}.broken"),
                ],
            )
        }
    )
    reloads = AsyncMock()
    load = AsyncMock(
        return_value=LoadedConfig(revision="1", document=config.model_dump_json())
    )
    pipeline = Pipeline(
        pg=postgres, schema=schema, config=config, reloads=reloads, load=load
    )

    monkeypatch.setattr(settings, "PG_DATABASE_URL", postgres.dsn)
    monkeypatch.setattr(settings, "S3_ENDPOINT", S3_ENDPOINT)
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", S3_ACCESS_KEY)
    monkeypatch.setattr(settings, "S3_SECRET_KEY", S3_SECRET_KEY)

    async def source_columns(pg_conn: object, table: object) -> list[tuple[str, str]]:
        return [("cpf", "BIGINT"), ("name", "VARCHAR")]

    monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)

    real_extraction = extraction.run_extraction

    async def counted(
        duckdb_conn: DuckDB, task: DumpTask, report: Callable[..., Awaitable[None]]
    ) -> None:
        pipeline.extractions.append(task.table)
        await real_extraction(duckdb_conn, task, report)

    monkeypatch.setattr(steps, "run_extraction", counted)

    for name in REAL_STEPS:
        body = workflow_body(getattr(steps, name))
        monkeypatch.setattr(steps, name, body)
        if hasattr(workflows, name):
            monkeypatch.setattr(workflows, name, body)

    async def enqueue(
        queue: str, workflow: Callable[..., Awaitable[object]], *args: object
    ) -> object:
        result = await workflow_body(workflow)(*args)

        async def get_result() -> object:
            return result

        return SimpleNamespace(
            get_result=get_result, workflow_id=f"workflow-{id(result)}-{len(args)}"
        )

    monkeypatch.setattr(
        workflows,
        "DBOS",
        SimpleNamespace(workflow_id="run-1", enqueue_workflow_async=enqueue),
    )
    monkeypatch.setattr(workflows, "SetEnqueueOptions", RecordedOptions)
    monkeypatch.setattr(workflows, "run_provision", AsyncMock())
    monkeypatch.setattr(workflows, "finalize_run", AsyncMock())
    monkeypatch.setattr(workflows, "reload_postgrest", reloads)
    monkeypatch.setattr(workflows, "load_sync_config", load)

    await initialize_schemas(postgres.backend, config)
    try:
        yield pipeline
    finally:
        cleanup = await psycopg.AsyncConnection.connect(postgres.dsn, autocommit=True)
        try:
            app = Identifier(settings.DBOS_APP_SCHEMA)
            for table in (pipeline.good, pipeline.broken):
                await cleanup.execute(
                    SQL("DELETE FROM {}.state WHERE table_name = %s").format(app),
                    (table,),
                )
            await cleanup.execute(
                SQL("DELETE FROM {}.scope WHERE schema_name = %s").format(app),
                (schema,),
            )
            await cleanup.execute(
                SQL("DELETE FROM {}.errors WHERE fields ->> 'table' LIKE %s").format(
                    app
                ),
                (f"p.{schema}.%",),
            )
        finally:
            await cleanup.close()
