"""PostgreSQL schema initialization and PostgREST reload operations."""

from psycopg import Connection
from psycopg.sql import Identifier

from .authorization import ensure_schema_policy_writer, schema_scope_predicate
from .models import SyncConfig
from .settings import settings
from .templates import execute_sql


def initialize_schemas(pg_conn: Connection, config: SyncConfig) -> None:
    """Create roles and application schemas before publication."""
    execute_sql(
        pg_conn,
        "postgres/init_roles",
        mapping={
            "user_role": Identifier(settings.AUTH_USER_ROLE),
            "authenticator_role": Identifier(settings.AUTH_AUTHENTICATOR_ROLE),
            "rls_schema": Identifier("rls"),
        },
    )

    for schema in config.schemas:
        execute_sql(
            pg_conn,
            "postgres/init_schema",
            mapping={
                "rls_schema": Identifier("rls"),
                "schema": Identifier(schema),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_predicate(schema),
            },
        )
        execute_sql(
            pg_conn,
            "postgres/init_access_policy",
            mapping={
                "schema": Identifier(schema),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_predicate(schema),
            },
        )
        ensure_schema_policy_writer(pg_conn, schema)

    pg_conn.commit()


def reload_postgrest(pg_conn: Connection, config: SyncConfig) -> None:
    """Revoke anonymous access and request a schema reload."""
    for schema in config.schemas:
        execute_sql(
            pg_conn,
            "postgres/revoke_anon",
            mapping={
                "schema": Identifier(schema),
                "anon_role": Identifier(settings.AUTH_ANON_ROLE),
            },
        )

    pg_conn.execute(b"NOTIFY pgrst, 'reload schema'")
