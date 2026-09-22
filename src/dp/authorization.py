"""PostgreSQL authorization and row-level security operations."""

from typing import assert_never

from psycopg import AsyncConnection
from psycopg.sql import Identifier, Literal

from .conditions import schema_scope_condition
from .executor import execute_sql
from .models import UnitMapping
from .settings import settings


async def ensure_schema_policy_writer(pg_conn: AsyncConnection, schema: str) -> None:
    """Create one schema policy-writer role when it is missing."""
    await execute_sql(
        pg_conn,
        "postgres/access_policy_writer",
        mapping={
            "schema": Identifier(schema),
            "policy_writer_role": Identifier(f"policy_writer_{schema}"),
            "policy_name": Identifier(f"policy_writer_{schema}"),
        },
    )


async def apply_table_authorization(
    pg_conn: AsyncConnection,
    schema: str,
    table_name: str,
    rls: list[UnitMapping] | None,
    claim: str | None,
) -> None:
    """Apply table grants and optional row-level security."""
    await execute_sql(
        pg_conn,
        "postgres/grant_select",
        mapping={
            "schema": Identifier(schema),
            "table": Identifier(table_name),
            "user_role": Identifier(settings.AUTH_USER_ROLE),
        },
    )

    match rls:
        case list():
            if claim is None:
                message = f"Schema {schema} has no configured identity claim for RLS"
                raise RuntimeError(message)
            await execute_sql(
                pg_conn,
                "postgres/access_policy_check",
                mapping={
                    "schema": Identifier(schema),
                    "table": Identifier(table_name),
                    "claim_setting": Literal(f"app.claim_{claim}"),
                    "rls_mappings": [
                        {"column": mapping.column, "unit_type": mapping.unit_type}
                        for mapping in rls
                    ],
                    "scope": schema_scope_condition(schema),
                },
            )
        case None:
            await execute_sql(
                pg_conn,
                "postgres/schema_scope_statement",
                mapping={
                    "schema": Identifier(schema),
                    "table": Identifier(table_name),
                    "scope": schema_scope_condition(schema),
                },
            )
        case _:
            assert_never(rls)
