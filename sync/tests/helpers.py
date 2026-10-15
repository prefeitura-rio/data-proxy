import base64
import hashlib
import hmac
import json
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
    POSTGREST_AUTH,
    ROUTED_TABLE,
    SILO_ENDPOINT,
    TEST_SQL_DIR,
)
from tests.fixtures.types import Postgres

type Selection = RangeSelection | TimeRangeSelection | RemainderSelection
type SqlParams = Mapping[str, DatabaseValue | list[str]]

DEFAULT_MODIFIED: Final = datetime(2025, 1, 1, tzinfo=UTC)


def schema_scope_condition(schema: str) -> Composable:
    """Return the schema-claim condition that the test policies use."""
    return SQL("{} = ANY(string_to_array(current_setting({}, true), ','))").format(
        Literal(schema), Literal("app.claim_schemas")
    )


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
        },
    )


async def call_table_function(pg: Postgres) -> tuple[list[DatabaseRow], dict[str, str]]:
    """Call the per-table function and return its rows and response headers."""
    rows = await fetch_all(
        pg,
        "postgres/select_table_function",
        mapping={"schema": pg.namespace.identifier},
    )
    return rows, await response_headers(pg)


async def can_execute(pg: Postgres, signature: str) -> bool:
    """Return whether the user role may run one function in the test schema."""
    return bool(
        await scalar(
            pg,
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            settings.AUTH_USER_ROLE,
            f"{pg.namespace.identifier.as_string(None)}.{signature}",
        )
    )


def helm_sql(name: str, mapping: Mapping[str, TemplateValue] | None = None) -> str:
    """Render one Helm SQL template as the init and maintenance jobs do."""
    return render_template(name, mapping or {}, root=HELM_SQL)


def psql_script(*statements: str) -> str:
    """Join rendered statements into one psql script."""
    return ";\n".join(statements) + ";\n"


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
        role = Identifier(f"policy_writer_{schema_name}")
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
            helm_sql(
                "setup_policy_writer",
                {"schema": schema, "policy_writer_role": role, "policy_name": role},
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
            "endpoint": SILO_ENDPOINT,
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


async def provision(connection: AsyncConnection, name: str) -> None:
    """Create a schema and its access policy, as the provision job does for a configured schema."""
    await connection.execute(
        SQL("CREATE SCHEMA IF NOT EXISTS {}").format(Identifier(name))
    )
    await connection.execute(
        SQL("CREATE TABLE IF NOT EXISTS {}.access_policy (id integer)").format(
            Identifier(name)
        )
    )


async def become(
    connection: AsyncConnection, role: str, schemas: str | None = None
) -> None:
    """Switch the session to one role, as PostgREST does for a JWT, with the schemas of its token."""
    if schemas is not None:
        await connection.execute(
            b"SELECT set_config('app.claim_schemas', %s, false)", (schemas,)
        )
    await connection.execute(SQL("SET ROLE {}").format(Identifier(role)))


def sign_token(claims: dict[str, str | list[str]]) -> str:
    """Sign an HS256 token with the secret of the test PostgREST instance."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).rstrip(b"=")
    message = header + b"." + payload
    signature = hmac.new(POSTGREST_AUTH.encode(), message, hashlib.sha256).digest()
    return f"{message.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
