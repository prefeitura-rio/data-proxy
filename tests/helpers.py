"""Shared test builders and assertions for the data-proxy test suite."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from google.cloud.bigquery import Row
from psycopg import AsyncConnection, AsyncCursor
from psycopg.sql import Composable

from data_proxy.kubernetes import Deployment
from data_proxy.models import (
    AllSelection,
    DumpTask,
    NonEmptyString,
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
    TableConfig,
    TaskSelection,
    TimeRangeSelection,
)
from data_proxy.templates import render_template
from data_proxy.types import DatabaseRow, TemplateValue
from tests.constants import FILES

TEST_SQL_DIR = FILES.parent / "templates"


def sync_plan(
    *,
    schema_name: str = "app",
    signatures: dict[str, str] | None = None,
    paths: dict[str, list[str]] | None = None,
    partitioned_tables: dict[str, PartitionedTablePlan] | None = None,
) -> SyncPlan:
    """Build one synchronization plan for tests."""
    return SyncPlan(
        schema_name=schema_name,
        signatures=signatures or {},
        paths=paths or {},
        partitioned_tables=partitioned_tables or {},
    )


def sync_config(
    tables: list[TableConfig],
    *,
    schema_name: str = "app",
    claim: NonEmptyString | None = None,
) -> SyncConfig:
    """Build one single-schema synchronization config for tests."""
    return SyncConfig(schemas={schema_name: SchemaConfig(tables=tables, claim=claim)})


def dump(
    *,
    run_id: str = "r1",
    table: str = "p.d.t",
    bucket_path: str = "s3://b/t",
    selections: list[TaskSelection] | None = None,
) -> DumpTask:
    """Build one common dump task for tests."""
    return DumpTask(
        run_id=run_id,
        table=table,
        target_schema="test",
        bucket_path=bucket_path,
        selections=selections or [AllSelection()],
    )


def partition(
    partition_id: str,
    signature: str = "signature",
    *,
    column: str = "cpf",
    width: int = 10,
    logical_bytes: int = 0,
) -> PhysicalPartition:
    """Build one normalized integer range partition for tests."""
    lower = int(partition_id)
    return PhysicalPartition(
        partition_id=partition_id,
        signature=signature,
        selection=RangeSelection(
            partition_id=partition_id,
            column=column,
            lower=lower,
            upper=lower + width,
        ),
        logical_bytes=logical_bytes,
    )


def planning_partition(
    partition_id: str,
    signature: str = "s",
    *,
    logical_bytes: int = 0,
) -> PhysicalPartition:
    """Build one planning partition, including the null remainder bucket."""
    selection = (
        RemainderSelection(column="id", start=0, end=1)
        if partition_id == "__NULL__"
        else RangeSelection(
            partition_id=partition_id,
            column="id",
            lower=int(partition_id),
            upper=int(partition_id) + 1,
        )
    )

    return PhysicalPartition(
        partition_id=partition_id,
        signature=signature,
        selection=selection,
        logical_bytes=logical_bytes,
    )


def render(value: object) -> str:
    """Render a mapping value expected to be a Psycopg SQL object3 object."""
    return cast(Composable, value).as_string(None)


async def execute_sql(
    connection: AsyncConnection,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: tuple[object, ...] = (),
) -> AsyncCursor[DatabaseRow]:
    """Execute a SQL fixture template, commit it, and return the cursor."""
    cursor = await connection.execute(
        render_template(path, mapping or {}, root=TEST_SQL_DIR),
        params,
    )
    await connection.commit()
    return cursor


async def fetch_all(
    connection: AsyncConnection,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: tuple[object, ...] = (),
) -> list[DatabaseRow]:
    """Execute a SQL fixture template and return every row."""
    cursor = await execute_sql(connection, path, mapping=mapping, params=params)
    return await cursor.fetchall()


async def fetch_one(
    connection: AsyncConnection,
    path: str,
    *,
    mapping: Mapping[str, TemplateValue] | None = None,
    params: tuple[object, ...] = (),
) -> DatabaseRow | None:
    """Execute a SQL fixture template and return one row."""
    cursor = await execute_sql(connection, path, mapping=mapping, params=params)
    return await cursor.fetchone()


def metadata_row(
    partition_id: str,
    logical_bytes: int,
    modified: datetime | None = None,
    missing_modified: bool = False,
) -> Row:
    """Build one BigQuery partition metadata row."""
    if modified is None:
        modified = datetime(2025, 1, 1, tzinfo=UTC)
    return cast(
        Row,
        cast(
            object,
            {
                "partition_id": partition_id,
                "last_modified_time": None if missing_modified else modified,
                "logical_bytes": logical_bytes,
            },
        ),
    )


def partition_for(
    selection: RangeSelection | TimeRangeSelection | RemainderSelection,
) -> PhysicalPartition:
    """Build a physical partition for one selection."""
    partition_id = (
        selection.partition_id if isinstance(selection, RangeSelection) else "partition"
    )
    return PhysicalPartition(
        partition_id=partition_id,
        signature="signature",
        selection=selection,
    )


async def deployment_value(value: Deployment) -> Deployment:
    """Return a deployment from a readiness test double."""
    return value
