"""Build the sync models and the state of the tables for the tests."""

from datetime import UTC, datetime
from typing import Final
from typing import Literal as ChangeLiteral

import pytest
from psycopg import AsyncConnection
from psycopg.sql import SQL, Identifier, Literal

from sync.models import (
    DumpTask,
    FullTable,
    PartitionChange,
    PartitionedTable,
    PartitionedTablePlan,
    SchemaConfig,
    Strategy,
    SyncConfig,
    TableConfig,
    TableState,
)
from sync.postgres import Postgres as PgBackend
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
from sync.types import (
    NonEmptyString,
)
from sync.views import stages
from sync.views.reconcile import reconcile_views
from tests.fixtures.types import Postgres
from tests.helpers.core import helm_sql
from tests.helpers.routing import schema_scope_condition

type Selection = RangeSelection | TimeRangeSelection | RemainderSelection


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


def partitioned_table() -> PartitionedTable:
    """Build the partitioned table p.d.t of the schema app, from the source bigquery."""
    return PartitionedTable(
        name="p.d.t", resolved_schema="app", resolved_source="bigquery"
    )


def ranged(index: int, size: int) -> PhysicalPartition:
    """Build the integer range partition that starts at index * 10, with a size in bytes."""
    lower = index * 10
    return partition(
        str(lower),
        RangeSelection(
            partition_id=str(lower), column="cpf", lower=lower, upper=lower + 10
        ),
        logical_bytes=size,
    )


def daily(day: int, size: int) -> PhysicalPartition:
    """Build the time partition of one day of September 2026, with a size in bytes."""
    return partition(
        f"202609{day:02d}",
        TimeRangeSelection(
            column="date",
            lower=f"2026-09-{day:02d}",
            upper=f"2026-09-{day + 1:02d}",
        ),
        logical_bytes=size,
    )


def universe(*partitions: PhysicalPartition) -> dict[str, PhysicalPartition]:
    """Index partitions by id, in the order that the source lists them."""
    return {item.partition_id: item for item in partitions}


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


DOCUMENT = SyncConfig(
    schemas={name: SchemaConfig() for name in ("app", "one", "other", "two")}
).model_dump_json()


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


async def reconcile_one_table(
    postgres: Postgres,
    monkeypatch: pytest.MonkeyPatch,
    columns: list[tuple[str, str]],
    *,
    fallback: bool = False,
) -> None:
    """Reconcile the views of one table whose BigQuery source reports the given columns."""
    schema = postgres.namespace.schema
    table: TableConfig = (
        PartitionedTable(name=f"p.{schema}.people", fallback=True)
        if fallback
        else FullTable(name=f"p.{schema}.people")
    )

    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})

    async def source_columns(
        _pg_conn: PgBackend, _table: TableConfig
    ) -> list[tuple[str, str]]:
        return columns

    monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)
    await initialize_schemas(postgres.backend, config)
    await reconcile_views(postgres.backend, config)


type ChangeKind = ChangeLiteral["add", "update", "remove", "same"]


def plan_of_kinds(
    stored: dict[str, ChangeKind], current: dict[str, ChangeKind]
) -> PartitionedTablePlan:
    """Build a plan from the kind of change of each partition id, where 'same' means no change."""
    partitions = {pid: partition(pid) for pid in {*stored, *current}}
    changes: dict[str, PartitionChange] = {}

    for pid, kind in {**stored, **current}.items():
        if kind == "same":
            continue
        changes[pid] = PartitionChange(
            kind=kind,
            partition_id=pid,
            current=None if kind == "remove" else partitions[pid],
            previous=partitions[pid] if kind in {"update", "remove"} else None,
            path="" if kind == "remove" else "p",
        )

    return table_plan(
        current={pid: partitions[pid] for pid in current}, changes=changes
    )
