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
    existing_views,
    reconcile_schema,
)


@dataclass
class ReconciliationContext:
    """Pipeline state for one view reconciliation run."""

    pg_conn: Postgres
    config: SyncConfig
    existing_views: set[tuple[str, str]] = field(default_factory=set)
    existing_functions: set[tuple[str, str]] = field(default_factory=set)
    changed: bool = False

    async def detect(self) -> None:
        """Compare desired views and functions against the database state."""
        desired = desired_views(self.config)
        desired_rpc = desired_functions(self.config)

        self.existing_views = await existing_views(
            self.pg_conn,
            list(self.config.schemas),
        )

        self.existing_functions = await existing_functions(
            self.pg_conn,
            list(self.config.schemas),
        )

        self.changed = (
            self.existing_views != desired or self.existing_functions != desired_rpc
        )

    async def cleanup(self) -> None:
        """Drop views and functions no longer in the sync config."""
        desired = desired_views(self.config)
        desired_rpc = desired_functions(self.config)
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
                f"postgres/sources/routing/{template}",
                mapping={"schema": app_schema()},
            )

    async def reconcile(self) -> None:
        """Reconcile every schema's tables and snapshot functions."""
        for schema_name in self.config.schemas:
            await reconcile_schema(self.pg_conn, schema_name, self.config)

    async def commit(self) -> bool:
        """Commit the transaction and return whether the view set changed."""
        await self.pg_conn.commit()
        return self.changed


async def reconcile_views(pg_conn: Postgres, config: SyncConfig) -> bool:
    """Reconcile configured PostgreSQL views and report whether their set changed."""
    ctx = ReconciliationContext(pg_conn=pg_conn, config=config)

    await ctx.detect()
    await ctx.cleanup()
    await ctx.install_routing()
    await ctx.reconcile()
    return await ctx.commit()
