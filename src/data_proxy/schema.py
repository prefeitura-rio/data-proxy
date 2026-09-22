"""PostgreSQL schema initialization and PostgREST reload operations."""

from psycopg import AsyncConnection
from psycopg.sql import Identifier

from .authorization import ensure_schema_policy_writer
from .conditions import schema_scope_condition
from .executor import execute_sql
from .models import SyncConfig
from .settings import settings


async def initialize_schemas(pg_conn: AsyncConnection, config: SyncConfig) -> None:
    """Create roles and application schemas before publication."""
    await execute_sql(
        pg_conn,
        "postgres/init_roles",
        mapping={"rls_schema": Identifier("rls")},
    )
    for procedure in (
        "cleanup_stale_objects",
        "apply_retention",
        "prune_access_log",
    ):
        await execute_sql(
            pg_conn,
            f"postgres/{procedure}",
            mapping={"schema": Identifier(settings.DBOS_APP_SCHEMA)},
        )

    for schema in config.schemas:
        await execute_sql(
            pg_conn,
            "postgres/init_schema",
            mapping={
                "rls_schema": Identifier("rls"),
                "schema": Identifier(schema),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_condition(schema),
            },
        )

        await execute_sql(
            pg_conn,
            "postgres/init_access_policy",
            mapping={
                "schema": Identifier(schema),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_condition(schema),
            },
        )

        await ensure_schema_policy_writer(pg_conn, schema)

    await pg_conn.commit()


async def revoke_anonymous_access(pg_conn: AsyncConnection, config: SyncConfig) -> None:
    """Revoke anonymous access before the PostgREST rollout refresh."""
    for schema in config.schemas:
        await execute_sql(
            pg_conn,
            "postgres/revoke_anon",
            mapping={
                "schema": Identifier(schema),
                "anonymous_role": Identifier(settings.AUTH_ANON_ROLE),
            },
        )

    await pg_conn.commit()
