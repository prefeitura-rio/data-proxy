"""Shared test builders and database helpers for the data-proxy test suite."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from inspect import unwrap
from pathlib import Path
from typing import Final, cast
from unittest.mock import AsyncMock

from lightkube import AsyncClient
from lightkube.models.apps_v1 import DeploymentSpec, DeploymentStatus
from lightkube.models.core_v1 import PodTemplateSpec
from lightkube.models.meta_v1 import LabelSelector, ObjectMeta
from lightkube.resources.apps_v1 import Deployment
from psycopg import AsyncCursor
from psycopg.rows import TupleRow
from psycopg.sql import Identifier, Literal

from data_proxy.conditions import schema_scope_condition
from data_proxy.dbos import workflows
from data_proxy.duckdb import DuckDB
from data_proxy.executor import Executor
from data_proxy.models import (
    AllSelection,
    DumpResult,
    DumpTask,
    NonEmptyString,
    PartitionChange,
    PartitionedTablePlan,
    PartitionMetadata,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncPlan,
    TableConfig,
    TableState,
    TaskSelection,
    TimeRangeSelection,
)
from data_proxy.settings import settings
from data_proxy.sources.sources import sources
from data_proxy.state import write_table_state
from data_proxy.templates import render_template
from data_proxy.types import (
    DatabaseRow,
    DatabaseValue,
    DuckDBParams,
    PostgresParams,
    TemplateValue,
)
from tests.constants import HELM_SQL, PARQUET, ROUTED_TABLE, TEST_SQL_DIR
from tests.fixtures.types import Postgres

type Selection = RangeSelection | TimeRangeSelection | RemainderSelection
type SqlParams = Mapping[str, DatabaseValue | list[str]]

DEFAULT_MODIFIED: Final = datetime(2025, 1, 1, tzinfo=UTC)


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


def deployment_client(
    *,
    status: DeploymentStatus | None,
    metadata: ObjectMeta | None,
    replicas: int | None,
) -> AsyncClient:
    """Return a client whose Deployment has the given rollout state."""
    client = AsyncMock(spec=AsyncClient)
    client.get.return_value = Deployment(
        metadata=metadata,
        spec=DeploymentSpec(
            selector=LabelSelector(), template=PodTemplateSpec(), replicas=replicas
        ),
        status=status,
    )
    return client


def workflow_body[**P, R](workflow: Callable[P, R]) -> Callable[P, R]:
    """Return the body of a DBOS workflow so it runs without the DBOS runtime."""
    return cast("Callable[P, R]", unwrap(workflow))


async def publish(plan: SyncPlan) -> set[str]:
    """Run the publish workflow body for one plan with no failed paths."""
    return await workflow_body(workflows.publish_schema)("run", plan, set())


async def run_dump_task(task: DumpTask) -> DumpResult:
    """Run the dump workflow body for one task."""
    return await workflow_body(workflows.dump_task)(task)


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
    """Set one session setting, as PostgREST and rls.pre_request do per request."""
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
    schema = pg.namespace.schema
    await Executor[PostgresParams, list[TupleRow]](conn=pg.backend).execute(
        "postgres/init_access_policy",
        mapping={
            "schema": pg.namespace.identifier,
            "user_role": Identifier(settings.AUTH_USER_ROLE),
            "scope": schema_scope_condition(schema),
        },
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
    """Store one table state in the test transaction, when there is one."""
    if state is not None:
        await write_table_state(pg.backend, table, state)


async def install_table_function(
    pg: Postgres, *, has_rls: bool, fallbacks: list[str]
) -> None:
    """Render the per-table function over the stub source helpers."""
    await Executor[PostgresParams, list[TupleRow]](conn=pg.backend).execute(
        "postgres/sources/create_function",
        mapping={
            "schema": pg.namespace.identifier,
            "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
            "function": Identifier("t_fn"),
            "dl_function": Identifier("t_dl_fn"),
            "columns": [
                {"name": column, "return_type": "text"}
                for column in ("source", "arg1", "arg2")
            ],
            "claim_setting": "app.claim_sub",
            "has_rls": str(has_rls).lower(),
            "rls_mappings": [{"column": "unit_id", "unit_type": "unit"}],
            "source_table": Literal(ROUTED_TABLE),
            "fallbacks": [
                {"name": name, "function": Identifier(f"t_{sources.get(name).suffix}")}
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
