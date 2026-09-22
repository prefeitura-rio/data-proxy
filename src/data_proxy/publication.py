"""Shadow loading, atomic table publication, and publication orchestration."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import LiteralString, assert_never, cast

from psycopg import AsyncConnection
from psycopg.sql import SQL, Composable, Identifier, Literal
from whenever import Instant

from .authorization import apply_table_authorization
from .conditions import partition_condition, scan_condition, schema_scope_condition
from .executor import execute_sql
from .fallback import duckdb_type_for, run_fallback_views_creation
from .freshness import (
    record_freshness_failures,
    update_published_freshness,
    upsert_freshness,
)
from .log import logger
from .models import (
    PartitionedTablePlan,
    PhysicalPartition,
    PublicationDecision,
    PublicationResult,
    SyncConfig,
    SyncPlan,
    SyncPublicationInput,
    TableConfig,
)
from .schema import initialize_schemas, revoke_anonymous_access
from .settings import settings
from .state import emit_error
from .types import DatabaseRow
from .utils import atomic


@dataclass(frozen=True, slots=True)
class ReplacePartitionsRoute:
    """Replace changed partitions in an existing table."""

    plan: PartitionedTablePlan


@dataclass(frozen=True, slots=True)
class ShadowSwapRoute:
    """Load a shadow table and atomically swap it into service."""


@dataclass(frozen=True, slots=True)
class CreateRoute:
    """Create a new table."""


async def table_exists(pg_conn: AsyncConnection, schema: str, table_name: str) -> bool:
    """Return whether one table already exists in the database."""
    cursor = await execute_sql(
        pg_conn,
        "postgres/table_exists",
        params=(schema, table_name),
    )
    row = await cursor.fetchone()

    return bool(row and row[0])


async def column_select_list(
    pg_conn: AsyncConnection, schema: str, table_name: str
) -> list[str]:
    """Return SQL-safe select expressions with explicit casts for one table."""
    cursor = await execute_sql(
        pg_conn,
        "postgres/column_types",
        params=(schema, table_name),
    )
    rows = cast("list[DatabaseRow]", await cursor.fetchall())
    rows = [(str(column), str(column_type)) for column, column_type in rows]

    return [
        SQL("r[{}]::{} AS {}")
        .format(
            Literal(col),
            SQL(cast(LiteralString, duckdb_type_for(typ))),
            Identifier(col),
        )
        .as_string(None)
        for col, typ in rows
    ]


async def insert_partition(
    pg_conn: AsyncConnection,
    schema: str,
    table_name: str,
    path: str,
    select_list: list[str],
    predicate: Composable,
) -> None:
    """Insert one Parquet partition into a partitioned parent table."""
    await execute_sql(
        pg_conn,
        "postgres/insert_partition",
        mapping={
            "temp": Identifier("_insert_partition"),
            "schema": Identifier(schema),
            "table": Identifier(table_name),
            "columns": select_list,
            "path": Literal(path),
            "predicate": predicate,
        },
    )


async def cast_json_columns_to_jsonb(
    pg_conn: AsyncConnection,
    schema: str,
    table_name: str,
) -> None:
    """Alter every json column on a table to jsonb before loading data."""
    cursor = await execute_sql(
        pg_conn,
        "postgres/json_columns",
        params=(schema, table_name),
    )
    rows = await cursor.fetchall()

    columns = [cast(str, row[0]) for row in rows]

    if not columns:
        return

    await execute_sql(
        pg_conn,
        "postgres/cast_json_to_jsonb",
        mapping={
            "schema": Identifier(schema),
            "table": Identifier(table_name),
            "columns": columns,
        },
    )


async def create_indexes(
    pg_conn: AsyncConnection, table: TableConfig, table_name: str
) -> None:
    """Create every configured index on a table."""
    for index in table.indexes:
        match index.method:
            case "btree":
                method = ""
            case "gin":
                method = " USING gin"

        columns = (
            list(index.expressions)
            if index.expressions is not None
            else list(index.columns)
        )

        await execute_sql(
            pg_conn,
            "postgres/create_index",
            mapping={
                "index": Identifier(index.name),
                "schema": Identifier(table.resolved_schema),
                "table": Identifier(table_name),
                "method": SQL(method),
                "columns": columns,
            },
        )


async def publish_table(pg_conn: AsyncConnection, table: TableConfig) -> None:
    """Atomically swap the shadow table into service, then create its indexes."""
    table_name = table.table_name
    shadow_name = f"{table_name}__next"

    await execute_sql(
        pg_conn,
        "postgres/swap_table",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table_name),
            "next_table": Identifier(shadow_name),
            "old_table": Identifier(f"{table_name}__old"),
        },
    )

    await create_indexes(pg_conn, table, table_name)


async def delete_partitions(
    pg_conn: AsyncConnection,
    table: TableConfig,
    affected: list[PhysicalPartition],
) -> None:
    """Delete rows in affected partitions from the live table."""
    predicates = [
        partition_condition(partition).as_string(None) for partition in affected
    ]
    await execute_sql(
        pg_conn,
        "postgres/delete_partitions",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table.table_name),
            "affected_partitions": predicates,
            "has_rls": bool(table.rls),
            "claim_setting": Literal(f"app.claim_{table.resolved_schema}"),
            "scope": schema_scope_condition(table.resolved_schema),
            "predicate": SQL("true"),
        },
    )


async def append_batch(
    pg_conn: AsyncConnection,
    table: TableConfig,
    table_name: str,
    path: str,
    select_list: list[str],
) -> None:
    """Append one Parquet file to an existing table."""
    await execute_sql(
        pg_conn,
        "postgres/append_batch",
        mapping={
            "temp": Identifier("_load_batch"),
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table_name),
            "path": Literal(path),
            "columns": select_list,
        },
    )


async def create_table_from_parquet(
    pg_conn: AsyncConnection,
    table: TableConfig,
    table_name: str,
    path: str,
) -> None:
    """Create and populate a table from a Parquet file."""
    await execute_sql(
        pg_conn,
        "postgres/create_table_from_parquet",
        mapping={
            "schema": Identifier(table.resolved_schema),
            "table": Identifier(table_name),
            "path": Literal(path),
        },
    )


async def load_table(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    table: TableConfig,
    table_name: str,
    paths: Sequence[str],
) -> None:
    """Create one table from the batch files and secure it, in one transaction."""
    schema_config = config.schemas.get(table.resolved_schema)

    async with atomic(pg_conn):
        await create_table_from_parquet(pg_conn, table, table_name, paths[0])
        select_list = await column_select_list(
            pg_conn, table.resolved_schema, table_name
        )

        await apply_table_authorization(
            pg_conn,
            table.resolved_schema,
            table_name,
            table.rls,
            schema_config.claim if schema_config else None,
        )

        for path in paths[1:]:
            await append_batch(pg_conn, table, table_name, path, select_list)

        await cast_json_columns_to_jsonb(pg_conn, table.resolved_schema, table_name)


async def rebuild_table(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    table: TableConfig,
    paths: Sequence[str],
) -> None:
    """Replace the live table with the batch files and create its indexes."""
    await load_table(pg_conn, config, table, table.table_name, paths)

    async with atomic(pg_conn):
        await create_indexes(pg_conn, table, table.table_name)


async def replace_partitions(
    pg_conn: AsyncConnection,
    table: TableConfig,
    table_plan: PartitionedTablePlan,
) -> None:
    """Replace every changed partition and drop every removed one in one transaction."""
    select_list = await column_select_list(
        pg_conn, table.resolved_schema, table.table_name
    )

    async with atomic(pg_conn):
        if table_plan.full_rebuild:
            await execute_sql(
                pg_conn,
                "postgres/delete_all_rows",
                mapping={
                    "schema": Identifier(table.resolved_schema),
                    "table": Identifier(table.table_name),
                },
            )
        else:
            affected = [
                table_plan.current_partitions[partition_id]
                for partition_id in table_plan.changed_paths
            ]
            affected.extend(table_plan.removed_partitions.values())

            if affected:
                await delete_partitions(pg_conn, table, affected)

        for partition_id, path in table_plan.changed_paths.items():
            partition = table_plan.current_partitions[partition_id]
            await insert_partition(
                pg_conn,
                table.resolved_schema,
                table.table_name,
                path,
                select_list,
                scan_condition(partition),
            )


async def prepare_table(
    pg_conn: AsyncConnection,
    config: SyncConfig,
    table: TableConfig,
    plan: SyncPlan,
    partitioned: PartitionedTablePlan | None,
) -> PreparedTable:
    """Prepare one table and report whether a shadow table waits for the swap."""
    paths = planned_paths(plan, table.name, partitioned)
    exists = await table_exists(pg_conn, table.resolved_schema, table.table_name)
    route = decide_route(exists, table, partitioned)

    match route:
        case ReplacePartitionsRoute(plan=table_plan):
            if not exists:
                message = (
                    f"Missing table {table.resolved_schema}.{table.table_name} "
                    "for an incremental plan"
                )
                raise RuntimeError(message)

            await replace_partitions(pg_conn, table, table_plan)
            return PreparedTable(table=table, swap=False)
        case ShadowSwapRoute():
            await load_table(pg_conn, config, table, f"{table.table_name}__next", paths)
            return PreparedTable(table=table, swap=True)
        case CreateRoute():
            await rebuild_table(pg_conn, config, table, paths)
            return PreparedTable(table=table, swap=False)


async def prepare_tables(
    pg_conn: AsyncConnection,
    dbos_conn: AsyncConnection,
    config: SyncConfig,
    plan: SyncPlan,
    changed: set[str],
) -> list[PreparedTable]:
    """Prepare, secure, and load each eligible table."""
    prepared: list[PreparedTable] = []

    for table in config.tables:
        if table.name not in changed:
            continue

        partitioned = plan.partitioned_tables.get(table.name)
        paths = planned_paths(plan, table.name, partitioned)

        logger.info(
            "Table preparation started table=%s path_count=%d",
            table.name,
            len(paths),
        )

        try:
            prepared_table = await prepare_table(
                pg_conn, config, table, plan, partitioned
            )
        except Exception:
            logger.exception("Table preparation failed table=%s", table.name)
            await emit_error(dbos_conn, "table_preparation_failed", table=table.name)
            continue

        logger.info("Table preparation completed table=%s", table.name)
        prepared.append(prepared_table)

    return prepared


@dataclass
class TablePublication:
    """Pipeline state for publishing one prepared table."""

    pg_conn: AsyncConnection
    dbos_conn: AsyncConnection
    table: TableConfig
    plan: SyncPlan
    attempted_at: Instant
    failed_partitions: dict[str, set[str]]
    swap: bool = False

    async def publish(self) -> None:
        """Swap the shadow table and update freshness."""
        if self.swap:
            await publish_table(self.pg_conn, self.table)

        await update_published_freshness(
            self.pg_conn,
            self.table,
            self.plan,
            self.failed_partitions.get(self.table.name, set()),
            self.attempted_at,
        )

    async def commit(self) -> None:
        """Commit the publication transaction."""
        await self.pg_conn.commit()

    async def fail(self) -> None:
        """Rollback, record the failure, and commit the failure state."""
        logger.exception("Table publication failed table=%s", self.table.name)
        await self.pg_conn.rollback()

        await emit_error(
            self.dbos_conn, "table_publication_failed", table=self.table.name
        )

        await record_freshness_failures(
            self.pg_conn, [self.table], self.plan, self.attempted_at
        )
        await upsert_freshness(
            self.pg_conn,
            self.table,
            self.failed_partitions.get(self.table.name, set()),
            self.attempted_at,
            success=False,
        )
        await self.pg_conn.commit()


@dataclass(frozen=True, slots=True)
class PreparedTable:
    """One prepared table and whether a shadow table waits for the swap."""

    table: TableConfig
    swap: bool


def failed_partition_ids(
    table_plan: PartitionedTablePlan, failed_paths: set[str]
) -> set[str]:
    """Return partition IDs for failed paths in one table plan."""
    return {
        partition_id
        for partition_id, path in table_plan.changed_paths.items()
        if path in failed_paths
    }


def apply_partition_fallback(
    table_plan: PartitionedTablePlan, failed_ids: set[str]
) -> None:
    """Keep prior manifest entries and omit failed new entries."""
    for partition_id in failed_ids:
        table_plan.changed_paths.pop(partition_id, None)
        previous = table_plan.previous_partitions.get(partition_id)

        if previous is None:
            table_plan.current_partitions.pop(partition_id, None)
            continue

        table_plan.current_partitions[partition_id] = previous


def reduce_sync_plan(plan: SyncPlan, failed_paths: set[str]) -> PublicationDecision:
    """Return the publishable plan and its extraction failures."""
    reduced = plan.model_copy(deep=True)
    blocked = {
        table for table, paths in plan.paths.items() if failed_paths.intersection(paths)
    }
    failed_partitions: dict[str, set[str]] = {}

    for table, table_plan in reduced.partitioned_tables.items():
        failed_ids = failed_partition_ids(table_plan, failed_paths)

        if not failed_ids:
            continue

        failed_partitions[table] = failed_ids

        if table_plan.full_rebuild:
            blocked.add(table)
        else:
            apply_partition_fallback(table_plan, failed_ids)

    return PublicationDecision(
        plan=reduced,
        blocked_tables=blocked,
        failed_partitions=failed_partitions,
    )


def planned_paths(
    plan: SyncPlan,
    table: str,
    partitioned: PartitionedTablePlan | None,
) -> list[str]:
    """Return ordinary or batch Parquet paths for one table."""
    match partitioned:
        case PartitionedTablePlan():
            return list(dict.fromkeys(partitioned.changed_paths.values()))
        case None:
            return plan.paths.get(table, [])
        case _:
            assert_never(partitioned)


def decide_route(
    exists: bool,
    table: TableConfig,
    partitioned: PartitionedTablePlan | None,
) -> ReplacePartitionsRoute | ShadowSwapRoute | CreateRoute:
    """Choose the publication route for one table."""
    match (exists, table, partitioned):
        case (_, _, PartitionedTablePlan(full_rebuild=False) as plan):
            return ReplacePartitionsRoute(plan=plan)
        case (True, _, _):
            return ShadowSwapRoute()
        case _:
            return CreateRoute()


async def run_publication_batch(
    pg_conn: AsyncConnection,
    dbos_conn: AsyncConnection,
    prepared: Sequence[PreparedTable],
    plan: SyncPlan,
    failed_partitions: dict[str, set[str]],
    attempted_at: Instant,
) -> set[str]:
    """Publish prepared tables and return those that succeeded."""
    published: set[str] = set()

    for prepared_table in prepared:
        table = prepared_table.table
        logger.info("Table publication started table=%s", table.name)

        ctx = TablePublication(
            pg_conn=pg_conn,
            dbos_conn=dbos_conn,
            table=table,
            plan=plan,
            attempted_at=attempted_at,
            failed_partitions=failed_partitions,
            swap=prepared_table.swap,
        )

        try:
            await ctx.publish()
            await ctx.commit()
        except Exception:
            await ctx.fail()
            continue

        logger.info("Table publication completed table=%s", table.name)
        published.add(table.name)

    return published


def empty_incremental_tables(plan: SyncPlan) -> set[str]:
    """Return incremental tables that have no publishable data changes."""
    return {
        table
        for table, table_plan in plan.partitioned_tables.items()
        if not table_plan.full_rebuild
        and not table_plan.changed_paths
        and not table_plan.removed_partitions
    }


@dataclass
class SyncContext:
    """Mutable pipeline state for one sync plan publication."""

    pg_conn: AsyncConnection
    dbos_conn: AsyncConnection
    config: SyncConfig
    plan: SyncPlan
    failed_paths: set[str] = field(default_factory=set)
    decision: PublicationDecision | None = None
    empty_incremental: set[str] = field(default_factory=set)
    eligible: set[str] = field(default_factory=set)
    attempted_at: Instant | None = None
    published: set[str] = field(default_factory=set)

    @property
    def tables_by_name(self) -> dict[str, TableConfig]:
        """Return a name-to-table lookup for the current config."""
        return {table.name: table for table in self.config.tables}

    def validate(self) -> None:
        """Compute the publication decision, empty incremental, and eligible set."""
        changed = SyncPublicationInput(
            config=self.config, plan=self.plan
        ).changed_tables
        self.decision = reduce_sync_plan(self.plan, self.failed_paths)
        self.empty_incremental = empty_incremental_tables(self.decision.plan)
        self.eligible = changed - self.decision.blocked_tables - self.empty_incremental

        logger.info(
            "Validated sync plan schema=%s changed=%d eligible=%d",
            self.plan.schema_name,
            len(changed),
            len(self.eligible),
        )

    async def prepare(self) -> None:
        """Initialize schemas and record extraction and empty-incremental failures."""
        if self.decision is None:
            raise RuntimeError("validate must be called before prepare")

        await initialize_schemas(self.pg_conn, self.config)
        logger.info("Initialized database schemas")
        self.attempted_at = Instant.now()

        await self.record_extraction_failures()

    async def record_extraction_failures(self) -> None:
        """Record blocked tables and incremental plans with no successful task."""
        if self.decision is None:
            raise RuntimeError(
                "validate must be called before recording extraction failures"
            )
        if self.attempted_at is None:
            raise RuntimeError(
                "prepare must be called before recording extraction failures"
            )

        tables = self.tables_by_name
        failed_tables = self.decision.blocked_tables | self.empty_incremental

        if failed_tables:
            for table_name in sorted(failed_tables):
                await emit_error(self.dbos_conn, "table_blocked", table=table_name)

        partitions_by_table = {
            table_name: self.decision.failed_partitions.get(table_name, set())
            for table_name in self.empty_incremental
        }

        await record_freshness_failures(
            self.pg_conn,
            [tables[name] for name in failed_tables],
            self.plan,
            self.attempted_at,
            partitions_by_table,
        )

    async def publish(self) -> None:
        """Prepare eligible tables and publish each successful result."""
        if self.decision is None:
            raise RuntimeError("validate must be called before publish")
        if self.attempted_at is None:
            raise RuntimeError("prepare must be called before publish")

        prepared = await prepare_tables(
            self.pg_conn,
            self.dbos_conn,
            self.config,
            self.decision.plan,
            self.eligible,
        )
        logger.info("Prepared %d tables", len(prepared))

        await self.record_preparation_failures(
            {prepared_table.table.name for prepared_table in prepared}
        )

        self.published = await run_publication_batch(
            self.pg_conn,
            self.dbos_conn,
            prepared,
            self.decision.plan,
            self.decision.failed_partitions,
            self.attempted_at,
        )
        logger.info("Published %d changed tables", len(self.published))

    async def record_preparation_failures(self, prepared_names: set[str]) -> None:
        """Record each eligible table that did not prepare successfully."""
        if self.attempted_at is None:
            raise RuntimeError(
                "prepare must be called before recording preparation failures"
            )

        tables = self.tables_by_name
        failed = [tables[name] for name in self.eligible - prepared_names]

        if failed:
            for table in failed:
                await emit_error(
                    self.dbos_conn, "table_preparation_failed", table=table.name
                )
            await record_freshness_failures(
                self.pg_conn, failed, self.plan, self.attempted_at
            )

    async def finalize(self) -> None:
        """Create fallback views and reload PostgREST after publication."""
        await run_fallback_views_creation(self.pg_conn, self.config)
        logger.info("Created BigQuery fallback views")

        await revoke_anonymous_access(self.pg_conn, self.config)
        logger.info("PostgREST schema reload requested")


async def run_publication(
    pg_conn: AsyncConnection,
    dbos_conn: AsyncConnection,
    config: SyncConfig,
    plan: SyncPlan,
    failed_paths: set[str] | None = None,
) -> PublicationResult:
    """Run the publication pipeline for one sync plan."""
    ctx = SyncContext(
        pg_conn=pg_conn,
        dbos_conn=dbos_conn,
        config=config,
        plan=plan,
        failed_paths=failed_paths or set(),
    )

    ctx.validate()
    await ctx.prepare()
    await ctx.publish()
    await ctx.finalize()

    if ctx.decision is None:
        raise RuntimeError("pipeline produced no decision")
    return PublicationResult(plan=ctx.decision.plan, published_tables=ctx.published)


async def configure_s3_secret(pg_conn: AsyncConnection) -> None:
    """Configure DuckDB S3 credentials on a Postgres connection."""
    await execute_sql(
        pg_conn,
        "postgres/configure_s3_secret",
        mapping={
            "s3_key_id": settings.S3_ACCESS_KEY,
            "s3_secret_key": settings.S3_SECRET_KEY,
            "s3_endpoint": settings.S3_ENDPOINT,
            "s3_use_ssl": "true" if settings.S3_USE_SSL else "false",
        },
    )
    await pg_conn.commit()
