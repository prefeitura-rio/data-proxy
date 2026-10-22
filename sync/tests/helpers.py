import itertools
import json
import tomllib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Final, Self, cast
from unittest.mock import AsyncMock

import duckdb
import pytest
from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import TupleRow
from psycopg.sql import SQL, Composable, Identifier, Literal
from pydantic import BaseModel, Field

from sync.dbos import workflows
from sync.duckdb import DuckDB
from sync.executor import Executor
from sync.models import (
    DumpResult,
    DumpTask,
    LoadedConfig,
    PartitionChange,
    PartitionedTablePlan,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncPlan,
    SyncWork,
    TableConfig,
    TableState,
)
from sync.postgres import Postgres as PgConnection
from sync.settings import settings
from sync.sources.bigquery.partitions import PartitionMetadata
from sync.sources.partitions import (
    AllSelection,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)
from sync.state import write_table_state
from sync.templates import render_template
from sync.types import (
    DatabaseRow,
    DatabaseValue,
    DuckDBParams,
    NonEmptyString,
    PostgresParams,
    TemplateValue,
)
from tests.constants import (
    HELM_SQL,
    PARQUET,
    RBAC_RULES,
    ROUTED_TABLE,
    S3_SECRET_INPUTS,
    TEST_SQL_DIR,
)
from tests.fixtures.types import Postgres

type Selection = RangeSelection | TimeRangeSelection | RemainderSelection
type SqlParams = Mapping[str, DatabaseValue | list[str]]

DEFAULT_MODIFIED: Final = datetime(2025, 1, 1, tzinfo=UTC)


def schema_scope_condition(schema: str) -> Composable:
    """Return the schema-claim condition that the test policies use, as the provision job renders it."""
    return SQL("rbac.mine({})").format(Literal(schema))


def sync_config(
    tables: list[TableConfig],
    *,
    schema_name: str = "app",
    claim: NonEmptyString | None = None,
) -> SyncConfig:
    """Build one single-schema synchronization config."""
    return SyncConfig(schemas={schema_name: SchemaConfig(tables=tables, claim=claim)})


def dump_task(
    *,
    run_id: str = "r1",
    table: str = "p.d.t",
    bucket_path: str = "s3://b/t",
    selections: list[TaskSelection] | None = None,
) -> DumpTask:
    """Build one dump task that extracts the whole table by default."""
    return DumpTask(
        run_id=run_id,
        table=table,
        source="bigquery",
        target_schema="app",
        bucket_path=bucket_path,
        selections=[AllSelection()] if selections is None else selections,
    )


def partition(
    partition_id: str,
    selection: Selection | None = None,
    *,
    signature: str = "signature",
    logical_bytes: int = 0,
) -> PhysicalPartition:
    """Build one physical partition.

    The default selection is the cpf range from the numeric ID to the ID plus 10.
    """
    if selection is None:
        lower = int(partition_id)
        selection = RangeSelection(
            partition_id=partition_id, column="cpf", lower=lower, upper=lower + 10
        )
    return PhysicalPartition(
        partition_id=partition_id,
        signature=signature,
        selection=selection,
        logical_bytes=logical_bytes,
    )


def partitioned(*days: int) -> TableState:
    """Build the state of a table with one time partition per day of September 2026."""
    return TableState(
        strategy=Strategy.PARTITIONED,
        signature="signature",
        partitions={
            f"202609{day:02d}": partition(
                f"202609{day:02d}",
                TimeRangeSelection(
                    column="date",
                    lower=f"2026-09-{day:02d}",
                    upper=f"2026-09-{day + 1:02d}",
                ),
            )
            for day in days
        },
    )


def metadata_row(
    partition_id: str,
    logical_bytes: int,
    modified: datetime | None = DEFAULT_MODIFIED,
) -> PartitionMetadata:
    """Build one validated BigQuery partition metadata row."""
    return PartitionMetadata(
        partition_id=partition_id,
        last_modified_time=modified,
        logical_bytes=logical_bytes,
    )


def table_plan(
    *,
    full_rebuild: bool = False,
    current: dict[str, PhysicalPartition] | None = None,
    changes: dict[str, PartitionChange] | None = None,
) -> PartitionedTablePlan:
    """Build one partitioned table plan."""
    return PartitionedTablePlan(
        table_signature="signature",
        full_rebuild=full_rebuild,
        current_partitions={} if current is None else current,
        changes={} if changes is None else changes,
    )


def add_change(
    partition_id: str, *, path: str = "", previous: PhysicalPartition | None = None
) -> PartitionChange:
    """Build one change that adds a partition."""
    return PartitionChange(
        kind="add",
        partition_id=partition_id,
        path=path,
        current=partition(partition_id),
        previous=previous,
    )


def remove_change(partition_id: str, previous: PhysicalPartition) -> PartitionChange:
    """Build one change that removes a partition."""
    return PartitionChange(kind="remove", partition_id=partition_id, previous=previous)


def workflow_body[**P, R](workflow: Callable[P, R]) -> Callable[P, R]:
    """Return the body of a DBOS workflow so it runs without the DBOS runtime."""
    return cast("Callable[P, R]", unwrap(workflow))


DOCUMENT = '{"schemas": {}}'


class RecordedOptions:
    """Stand in for SetEnqueueOptions and remember the options that are active."""

    active: ClassVar[dict[str, object]] = {}
    options: dict[str, object]

    def __init__(self, **options: object) -> None:
        """Keep the options of one enqueue."""
        self.options = options

    def __enter__(self) -> Self:
        """Make the options active."""
        RecordedOptions.active = self.options
        return self

    def __exit__(self, *_: object) -> None:
        """Clear the options."""
        RecordedOptions.active = {}


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
                plans=plans or [SyncPlan(schema_name="app")], tasks=tasks or []
            )
        ),
    )
    monkeypatch.setattr(
        workflows,
        "seed_schemas",
        AsyncMock(return_value=postgrest_reload_required),
    )
    monkeypatch.setattr(workflows, "record_seed_metrics", AsyncMock())
    monkeypatch.setattr(workflows, "record_scope", AsyncMock())
    monkeypatch.setattr(workflows, "run_provision", AsyncMock())
    monkeypatch.setattr(workflows, "reload_postgrest", restart)
    monkeypatch.setattr(workflows, "finalize_run", AsyncMock())
    return calls, events


async def run_sync(context: dict[str, list[str]] | None = None) -> None:
    """Run the sync workflow body."""
    await workflow_body(workflows.run_sync)(datetime.now(UTC), context)


async def attach_ducklake(duckdb: DuckDB, tmp_path: Path) -> None:
    """Attach one file-based DuckLake catalog for the test connection."""
    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
        "duckdb/attach",
        mapping={
            "catalog": Literal(f"ducklake:sqlite:{tmp_path / 'catalog.sqlite'}"),
            "data_path": Literal(str(tmp_path / "data")),
            "encrypted": False,
        },
    )


async def create_ducklake_table(
    duckdb: DuckDB, table: str, parquet: str = PARQUET
) -> None:
    """Create one DuckLake table from a Parquet schema."""
    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
        "duckdb/create_table",
        mapping={"table": Identifier(table)},
        params=[parquet],
    )


async def ducklake_row_count(duckdb: DuckDB, table: str) -> int:
    """Return the row count of one DuckLake table."""
    rows = await duckdb.query(
        render_template(
            "duckdb/row_count", {"table": Identifier(table)}, root=TEST_SQL_DIR
        )
    )
    row_count = rows[0][0]
    assert isinstance(row_count, int)
    return row_count


async def ducklake_columns(duckdb: DuckDB, table: str) -> set[str]:
    """Return the column names of one DuckLake table."""
    rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
        "duckdb/describe_table",
        mapping={"table": Identifier(table)},
        expect=tuple[str, str],
    )
    return {row[0] for row in rows}


async def execute_sql(
    pg: Postgres,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: SqlParams | None = None,
) -> AsyncCursor[DatabaseRow]:
    """Execute one SQL test template in the current transaction."""
    return await pg.connection.execute(
        render_template(path, mapping or {}, root=TEST_SQL_DIR), params
    )


async def fetch_all(
    pg: Postgres,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: SqlParams | None = None,
) -> list[DatabaseRow]:
    """Execute one SQL test template and return every row."""
    cursor = await execute_sql(pg, path, mapping=mapping, params=params)
    return await cursor.fetchall()


async def fetch_one(
    pg: Postgres,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: SqlParams | None = None,
) -> DatabaseRow | None:
    """Execute one SQL test template and return its first row."""
    cursor = await execute_sql(pg, path, mapping=mapping, params=params)
    return await cursor.fetchone()


async def scalar(
    pg: Postgres, sql: str, *params: DatabaseValue | list[str]
) -> DatabaseValue:
    """Run one short inline statement and return its first value."""
    cursor = await pg.connection.execute(sql.encode(), params)
    row = await cursor.fetchone()
    assert row is not None
    return cast("DatabaseValue", row[0])


async def set_setting(pg: Postgres, name: str, value: str) -> None:
    """Set one session setting, as PostgREST and data_proxy.pre_request do per request."""
    await pg.connection.execute("SELECT set_config(%s, %s, false)", (name, value))


async def set_request_headers(pg: Postgres, headers: dict[str, str]) -> None:
    """Set the request headers as PostgREST does."""
    await set_setting(pg, "request.headers", json.dumps(headers))


async def response_headers(pg: Postgres) -> dict[str, str]:
    """Return the response headers as one mapping.

    PostgREST reads response.headers as a JSON list of one-entry objects.
    """
    raw = await scalar(pg, "SELECT current_setting('response.headers', true)")
    sent: list[dict[str, str]] = json.loads(str(raw)) if raw else []
    return {name: value for header in sent for name, value in header.items()}


async def relation_exists(pg: Postgres, schema: str, relation: str) -> bool:
    """Return whether one table or view exists in a schema."""
    row = await fetch_one(
        pg,
        "postgres/relation_exists",
        params={"relation": Identifier(schema, relation).as_string(None)},
    )
    return row == (True,)


async def function_exists(pg: Postgres, schema: str, signature: str) -> bool:
    """Return whether one function signature exists in a schema."""
    row = await fetch_one(
        pg,
        "postgres/function_exists",
        params={"signature": f"{Identifier(schema).as_string(None)}.{signature}"},
    )
    return row == (True,)


async def create_access_policy(pg: Postgres) -> None:
    """Create the production access_policy and access_log tables in the test schema."""
    await pg.connection.execute(
        helm_sql(
            "setup_access_policy",
            {
                "schema": pg.namespace.identifier,
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_condition(pg.namespace.schema),
                "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
            },
        ).encode()
    )


async def insert_access_policy(
    pg: Postgres, subject: str, unit_type: str, unit_id: str
) -> None:
    """Insert one access-policy grant in the test schema."""
    await execute_sql(
        pg,
        "postgres/insert_access_policy",
        mapping={"schema": pg.namespace.identifier},
        params={"subject": subject, "unit_type": unit_type, "unit_id": unit_id},
    )


async def grant_unit(pg: Postgres, subject: str) -> None:
    """Act as the subject and grant it the unit u1."""
    await set_setting(pg, "app.claim_sub", subject)
    await insert_access_policy(pg, subject, "unit", "u1")


def psql_script(*statements: str) -> str:
    """Join rendered statements into one psql script."""
    return ";\n".join(statements) + ";\n"


async def put_state(pg: Postgres, table: str, state: TableState | None) -> None:
    """Set one table state or remove its committed state for this test."""
    if state is None:
        await pg.connection.execute(
            SQL("DELETE FROM {}.state WHERE table_name = {}").format(
                Identifier(settings.DBOS_APP_SCHEMA),
                Literal(table),
            )
        )
        return

    await write_table_state(pg.backend, table, state)


async def install_table_function(
    pg: Postgres, *, has_rls: bool, fallbacks: list[str]
) -> None:
    """Render the per-table function over the stub source helpers."""
    from sync.views.mappings import function_columns

    columns = function_columns(
        [("source", "VARCHAR"), ("arg1", "VARCHAR"), ("arg2", "VARCHAR")],
        raw_json=True,
    )
    await Executor[PostgresParams, list[TupleRow]](conn=pg.backend).execute(
        "postgres/views/create_function",
        mapping={
            "schema": pg.namespace.identifier,
            "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
            "function": Identifier("t_fn"),
            "dl_function": Identifier("t_dl_fn"),
            "dl_view": "dl_t",
            "columns": columns,
            "claim_setting": "app.claim_sub",
            "has_rls": str(has_rls).lower(),
            "rls_mappings": [{"column": "unit_id", "unit_type": "unit"}],
            "source_table": Literal(ROUTED_TABLE),
            "fallbacks": [
                {
                    "name": name,
                    "function": Identifier(
                        f"t_{'bq_fn' if name == 'bigquery' else name}"
                    ),
                    "view": "source_t",
                }
                for name in fallbacks
            ],
            "user_role": Identifier(settings.AUTH_USER_ROLE),
            "schema_literal": Literal(pg.namespace.schema),
        },
    )


async def act_as_token(pg: Postgres, role: str, schemas: str) -> None:
    """Give the connection the permissions of one role and the schemas claim of a token.

    The settings last for the whole session, so they survive a commit. rbac.load()
    sets them for one transaction, as the pre-request function does for a request.
    """
    await pg.connection.execute(
        """
        SELECT
            set_config(
                'rbac.permissions',
                coalesce((SELECT wrapped FROM rbac.roles WHERE name = %s), ''),
                false
            ),
            set_config('rbac.schemas', ',' || %s || ',', false)
        """,
        (role, schemas),
    )


async def call_table_function(
    pg: Postgres, role: str = "dp_api", schemas: str | None = None
) -> tuple[list[DatabaseRow], dict[str, str]]:
    """Call the per-table function as a token with one role and, by default, the test schema in its claim."""
    await act_as_token(pg, role, pg.namespace.schema if schemas is None else schemas)
    rows = await fetch_all(
        pg,
        "postgres/select_table_function",
        mapping={"schema": pg.namespace.identifier},
    )
    return rows, await response_headers(pg)


def helm_sql(name: str, mapping: Mapping[str, TemplateValue] | None = None) -> str:
    """Render one Helm SQL template as the init and maintenance jobs do."""
    return render_template(name, mapping or {}, root=HELM_SQL)


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


async def create_state_tables(connection: AsyncConnection) -> None:
    """Create the application schema with the state and errors tables, as the configure job does."""
    await connection.execute(
        helm_sql(
            "create_state", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
        ).encode()
    )


async def initialize_schemas(pg_conn: PgConnection, config: SyncConfig) -> bool:
    """Create the shared objects and each application schema, as the configure job does."""
    app_schema = Identifier(settings.DBOS_APP_SCHEMA)
    await create_state_tables(pg_conn.connection)
    for procedure in ("cleanup_stale_objects", "prune_access_log"):
        await pg_conn.connection.execute(
            helm_sql(procedure, {"schema": app_schema}).encode()
        )

    for schema_name in config.schemas:
        schema = Identifier(schema_name)
        for statement in (
            helm_sql("create_schema", {"schema": schema}),
            helm_sql(
                "setup_access_policy",
                {
                    "schema": schema,
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                    "scope": schema_scope_condition(schema_name),
                    "app_schema": app_schema,
                },
            ),
        ):
            await pg_conn.connection.execute(statement.encode())

    await pg_conn.commit()
    return True


def publish_people_catalog(metadata_schema: str, data_path: str) -> None:
    """Write the people catalog with the DuckDB of the sync worker, not the database one."""
    script = render_template(
        "duckdb/publish_people_snapshots",
        {
            "catalog_url": settings.DUCKLAKE_CATALOG_URL,
            "metadata_schema": metadata_schema,
            "data_path": data_path,
            **S3_SECRET_INPUTS,
            "first": "s3://test-bucket/app/people/people_partition_10.parquet",
            "second": "s3://test-bucket/app/people/people_partition_20.parquet",
        },
        root=TEST_SQL_DIR,
    )
    connection = duckdb.connect()
    try:
        for statement in filter(str.strip, script.split(";\n")):
            connection.execute(statement)
    finally:
        connection.close()


async def rows(connection: AsyncConnection, sql: str) -> list[tuple[object, ...]]:
    """Return every row of one inline query."""
    cursor = await connection.execute(sql.encode())
    return await cursor.fetchall()


class RbacRole(BaseModel):
    """One role of the rules file."""

    name: str
    description: str
    inherits: list[str] = Field(default_factory=list)
    permissions: list[str]


class RbacRules(BaseModel):
    """The rules file that the create_rbac template turns into SQL."""

    permissions: dict[str, dict[str, str]]
    roles: list[RbacRole]

    @property
    def features(self) -> list[str]:
        """Return every permission name, the action and the object joined by an underscore."""
        return sorted(
            f"{action}_{subject}"
            for subject, actions in self.permissions.items()
            for action in actions
            if action != "description"
        )

    def effective(self, role: str) -> set[str]:
        """Return the permissions of a role and of every role that it inherits."""
        by_name = {candidate.name: candidate for candidate in self.roles}
        pending, seen, found = [role], set[str](), set[str]()
        while pending:
            name = pending.pop()
            if name in seen or name not in by_name:
                continue
            seen.add(name)
            found.update(by_name[name].permissions)
            pending.extend(by_name[name].inherits)

        return found

    def changed(self, role: str, **update: list[str]) -> RbacRules:
        """Return a copy in which one role has other values."""
        return self.model_copy(
            update={
                "roles": [
                    candidate.model_copy(update=update)
                    if candidate.name == role
                    else candidate
                    for candidate in self.roles
                ]
            }
        )

    def context(self) -> Mapping[str, TemplateValue]:
        """Return the rules as the template context."""
        return {
            "permissions": {
                subject: dict(actions) for subject, actions in self.permissions.items()
            },
            "roles": [
                {
                    "name": role.name,
                    "description": role.description,
                    "inherits": list(role.inherits),
                    "permissions": list(role.permissions),
                }
                for role in self.roles
            ],
        }


RBAC: Final = RbacRules.model_validate(tomllib.loads(RBAC_RULES.read_text()))


SOURCE_HASH: Final = "0f" * 32


async def install_rbac(
    pg: Postgres, rules: RbacRules = RBAC, source_hash: str = SOURCE_HASH
) -> None:
    """Render create_rbac with a rules file and run it in the test transaction."""
    await pg.connection.execute(
        helm_sql(
            "create_rbac",
            {
                "rules": rules.context(),
                "anonymous_role": Identifier("anon"),
                "source_hash": source_hash,
            },
        ).encode()
    )


async def stored_roles(pg: Postgres) -> list[tuple[str, list[str]]]:
    """Return the rows of rbac.roles."""
    cursor = await pg.connection.execute(
        b"SELECT name, permissions FROM rbac.roles ORDER BY name"
    )
    return [(name, permissions) for name, permissions in await cursor.fetchall()]


async def install_console_triggers(pg: Postgres) -> None:
    """Create the console tables that the triggers watch, and install the triggers, in the test transaction."""
    await pg.connection.execute(
        b"""
        CREATE SCHEMA console;
        CREATE TABLE console.schemas (name text);
        CREATE TABLE console.tables (name text);
        CREATE TABLE console.rls (id integer)
        """
    )
    await pg.connection.execute(helm_sql("create_console_triggers").encode())


async def queued_workflows(pg: Postgres, dbos_schema: str) -> list[str]:
    """Return the names of the workflows that wait in the DBOS queues."""
    cursor = await pg.connection.execute(
        SQL(
            """
            SELECT name FROM {}.workflow_status
            WHERE status = 'ENQUEUED' AND name IN ('reload_proxy', 'provision_schemas', 'cleanup_tables')
            """
        ).format(Identifier(dbos_schema))
    )
    return sorted(itertools.starmap(str, await cursor.fetchall()))


async def revision(pg: Postgres) -> int:
    """Return the revision of the rules and of the access policy."""
    value = await scalar(pg, "SELECT value FROM rbac.revision")
    assert isinstance(value, int)
    return value


async def plan_of(pg: Postgres, query: str) -> str:
    """Return the verbose plan of a query as one text."""
    cursor = await pg.connection.execute(
        f"EXPLAIN (VERBOSE, COSTS OFF) {query}".encode()
    )
    return "\n".join(str(row[0]) for row in await cursor.fetchall())


SERVING: Final = "auth_v"


async def make_serving_login(pg: Postgres) -> None:
    """Create a serving login that is a member of every role of the rules and of the anonymous role."""
    await pg.connection.execute(
        SQL("CREATE ROLE {} LOGIN NOINHERIT").format(Identifier(SERVING))
    )
    for role in (*(role.name for role in RBAC.roles), "anon"):
        await pg.connection.execute(
            SQL("GRANT {} TO {}").format(Identifier(role), Identifier(SERVING))
        )


async def verify_rbac(pg: Postgres, extra_roles: tuple[str, ...] = ()) -> None:
    """Render verify_rbac for the roles of the rules, and run it in the test transaction."""
    await pg.connection.execute(
        helm_sql(
            "verify_rbac",
            {
                "roles": SQL(", ").join(
                    Literal(role)
                    for role in (*(role.name for role in RBAC.roles), *extra_roles)
                ),
                "anonymous_role": Literal("anon"),
                "serving_role": Literal(SERVING),
            },
        ).encode()
    )
