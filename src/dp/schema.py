"""PostgreSQL schema initialization and PostgREST reload operations."""

from collections.abc import Callable

from psycopg import AsyncConnection
from psycopg.sql import Identifier

from .authorization import ensure_schema_policy_writer
from .conditions import schema_scope_condition
from .executor import execute_sql
from .models import SchemaConfig, SyncConfig, SyncPlan
from .settings import settings


async def initialize_schemas(pg_conn: AsyncConnection, config: SyncConfig) -> None:
    """Create roles and application schemas before publication."""
    await execute_sql(
        pg_conn,
        "postgres/init_roles",
        mapping={"rls_schema": Identifier("rls")},
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


async def initialize_schemas_for_plans(
    plans: list[SyncPlan],
    writers_dsn: Callable[[str], str],
    sync_schemas: dict[str, SchemaConfig],
) -> None:
    """Group schemas by writer DSN and initialize each group."""
    by_dsn: dict[str, list[str]] = {}

    for plan in plans:
        by_dsn.setdefault(writers_dsn(plan.schema_name), []).append(plan.schema_name)

    for dsn, schemas in by_dsn.items():
        async with await AsyncConnection.connect(dsn) as conn:
            await initialize_schemas(
                conn,
                SyncConfig(schemas={name: sync_schemas[name] for name in schemas}),
            )


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
