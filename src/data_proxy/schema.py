"""PostgreSQL schema initialization and PostgREST reload operations."""

from psycopg.rows import TupleRow
from psycopg.sql import Identifier

from .authorization import ensure_schema_policy_writer
from .conditions import schema_scope_condition
from .executor import Executor
from .models import SyncConfig
from .postgres import Postgres
from .settings import settings
from .types import PostgresParams


def schema() -> Identifier:
    """Return the application state schema as a SQL identifier."""
    return Identifier(settings.DBOS_APP_SCHEMA)


async def initialize_schemas(pg_conn: Postgres, config: SyncConfig) -> bool:
    """Create roles and application schemas before publication."""
    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)
    for procedure in ("cleanup_stale_objects", "prune_access_log"):
        await executor.execute(
            f"postgres/{procedure}",
            mapping={"schema": Identifier(settings.DBOS_APP_SCHEMA)},
        )

    for schema in config.schemas:
        await executor.execute(
            "postgres/init_schema",
            mapping={
                "schema": Identifier(schema),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_condition(schema),
            },
        )
        await executor.execute(
            "postgres/init_access_policy",
            mapping={
                "schema": Identifier(schema),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_condition(schema),
            },
        )
        await ensure_schema_policy_writer(pg_conn, schema)

    await pg_conn.commit()
    return True


async def revoke_anonymous_access(pg_conn: Postgres, config: SyncConfig) -> None:
    """Revoke anonymous access before the PostgREST rollout refresh."""
    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)
    for schema in config.schemas:
        await executor.execute(
            "postgres/revoke_anon",
            mapping={
                "schema": Identifier(schema),
                "anonymous_role": Identifier(settings.AUTH_ANON_ROLE),
            },
        )

    await pg_conn.commit()
