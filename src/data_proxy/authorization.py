"""PostgreSQL authorization and row-level security operations."""

from typing import assert_never

from psycopg.rows import TupleRow
from psycopg.sql import Identifier, Literal

from .conditions import schema_scope_condition
from .executor import Executor
from .models import UnitMapping
from .postgres import Postgres
from .settings import settings
from .types import PostgresParams


async def ensure_schema_policy_writer(pg_conn: Postgres, schema: str) -> None:
    """Create one schema policy-writer role when it's missing."""
    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)
    await executor.execute(
        "postgres/access_policy_writer",
        mapping={
            "schema": Identifier(schema),
            "policy_writer_role": Identifier(f"policy_writer_{schema}"),
            "policy_name": Identifier(f"policy_writer_{schema}"),
        },
    )


async def apply_table_authorization(
    pg_conn: Postgres,
    schema: str,
    table_name: str,
    rls: list[UnitMapping] | None,
    claim: str | None,
) -> None:
    """Apply table grants and optional row-level security."""
    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)
    await executor.execute(
        "postgres/grant_select",
        mapping={
            "schema": Identifier(schema),
            "object": Identifier(table_name),
            "user_role": Identifier(settings.AUTH_USER_ROLE),
        },
    )

    match rls:
        case list():
            if claim is None:
                message = f"Schema {schema} has no configured identity claim for RLS"
                raise RuntimeError(message)
            await executor.execute(
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
            await executor.execute(
                "postgres/schema_scope_statement",
                mapping={
                    "schema": Identifier(schema),
                    "table": Identifier(table_name),
                    "scope": schema_scope_condition(schema),
                },
            )
        case _:
            assert_never(rls)
