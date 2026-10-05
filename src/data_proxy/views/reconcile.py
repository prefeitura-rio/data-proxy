"""Source view reconciliation pipeline."""

from dataclasses import dataclass, field

from psycopg.rows import TupleRow

from ..executor import Executor
from ..models import SyncConfig
from ..postgres import Postgres
from ..schema import schema as app_schema
from ..types import PostgresParams
from .stages import (
    desired_functions,
    desired_views,
    drop_removed_functions,
    drop_removed_views,
    existing_functions,
    existing_view_signatures,
    existing_views,
    reconcile_schema,
)


@dataclass
class ReconciliationContext:
    """Pipeline state for one view reconciliation run."""

    pg_conn: Postgres
    config: SyncConfig
    schema_names: list[str]
    table_names: dict[str, set[str]] | None = None
    existing_views: set[tuple[str, str]] = field(default_factory=set)
    existing_functions: set[tuple[str, str]] = field(default_factory=set)
    existing_view_signatures: dict[tuple[str, str], str | None] = field(
        default_factory=dict
    )
    postgrest_restart_required: bool = False

    async def detect(self) -> None:
        """Compare desired views and functions against the database state."""
        selected = set(self.schema_names)
        desired = desired_views(self.config, selected)
        desired_rpc = desired_functions(self.config, selected)

        self.existing_views = await existing_views(self.pg_conn, self.schema_names)
        self.existing_functions = await existing_functions(
            self.pg_conn, self.schema_names
        )
        self.existing_view_signatures = await existing_view_signatures(
            self.pg_conn, self.schema_names
        )
        self.postgrest_restart_required = (
            self.existing_views != desired or self.existing_functions != desired_rpc
        )

    async def cleanup(self) -> None:
        """Drop views and functions no longer in the sync config."""
        selected = set(self.schema_names)
        desired = desired_views(self.config, selected)
        desired_rpc = desired_functions(self.config, selected)
        await drop_removed_views(self.pg_conn, self.existing_views - desired)
        await drop_removed_functions(
            self.pg_conn,
            self.existing_functions - desired_rpc,
        )

    async def install_routing(self) -> None:
        """Install the orchestration functions that route requests to sources."""
        executor = Executor[PostgresParams, list[TupleRow]](conn=self.pg_conn)
        for template in ("snapshot", "coverage", "plan", "response"):
            await executor.execute(
                f"postgres/views/routing/{template}",
                mapping={"schema": app_schema()},
            )

    async def reconcile(self) -> None:
        """Reconcile changed generated serving objects in planned schemas."""
        for schema_name in self.schema_names:
            table_names = (
                None
                if self.table_names is None
                else self.table_names.get(schema_name, set())
            )

            changed = await reconcile_schema(
                self.pg_conn,
                schema_name,
                self.config,
                self.existing_view_signatures,
                self.existing_functions,
                table_names,
            )

            self.postgrest_restart_required = self.postgrest_restart_required or changed

    async def commit(self) -> bool:
        """Commit and report whether Kubernetes must restart PostgREST."""
        await self.pg_conn.commit()
        return self.postgrest_restart_required


async def reconcile_views(
    pg_conn: Postgres,
    config: SyncConfig,
    schema_names: list[str] | None = None,
    table_names: dict[str, set[str]] | None = None,
) -> bool:
    """Reconcile planned PostgreSQL schemas and report a restart requirement."""
    selected = sorted(config.schemas) if schema_names is None else schema_names
    ctx = ReconciliationContext(
        pg_conn=pg_conn,
        config=config,
        schema_names=selected,
        table_names=table_names,
    )

    await ctx.detect()
    await ctx.cleanup()
    await ctx.install_routing()
    await ctx.reconcile()
    return await ctx.commit()
