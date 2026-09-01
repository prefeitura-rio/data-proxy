"""PostgreSQL authorization and row-level security operations."""

from typing import assert_never

from psycopg import Connection
from psycopg.sql import SQL, Composable, Identifier, Literal

from .models import UnitMapping
from .settings import settings
from .templates import TemplateSpec, load_template


def claim_session_var(claim: str) -> Literal:
    """Return the session variable for one mirrored JWT claim."""
    return Literal(f"app.claim_{claim}")


def schema_scope_predicate(schema: str) -> Composable:
    """Return the predicate that requires one schema claim."""
    return SQL("{} = ANY(string_to_array(current_setting({}, true), ','))").format(
        Literal(schema), claim_session_var("schemas")
    )


def unit_predicate(mappings: list[UnitMapping]) -> Composable:
    """Return the predicate that matches any configured unit column."""
    return SQL(" OR ").join(
        SQL("(p.unit_type = {} AND p.unit_id = {}::text)").format(
            Literal(mapping.unit_type), Identifier(mapping.column)
        )
        for mapping in mappings
    )


def table_access_policy_statement(
    schema: str,
    table_name: str,
    rls: list[UnitMapping],
    claim: str,
) -> str:
    """Render one access-policy RLS statement."""
    return load_template(
        TemplateSpec(
            path="pg/access_policy_check",
            mapping={
                "schema": Identifier(schema),
                "table": Identifier(table_name),
                "session_var": claim_session_var(claim),
                "predicate": unit_predicate(rls),
                "scope": schema_scope_predicate(schema),
            },
        )
    )


def schema_scope_statement(schema: str, table_name: str) -> str:
    """Render one schema-scope RLS statement."""
    return load_template(
        TemplateSpec(
            path="pg/schema_scope_check",
            mapping={
                "schema": Identifier(schema),
                "table": Identifier(table_name),
                "scope": schema_scope_predicate(schema),
            },
        )
    )


def access_policy_writer_statement(schema: str) -> str:
    """Render one schema policy-writer statement."""
    return load_template(
        TemplateSpec(
            path="pg/access_policy_writer",
            mapping={
                "schema": Identifier(schema),
                "policy_writer_role": Identifier(f"policy_writer_{schema}"),
                "authenticator_role": Identifier(settings.AUTH_AUTHENTICATOR_ROLE),
                "policy_name": Identifier(f"policy_writer_{schema}"),
            },
        )
    )


def ensure_schema_policy_writer(pg_conn: Connection, schema: str) -> None:
    """Create one schema policy-writer role when it is missing."""
    pg_conn.execute(access_policy_writer_statement(schema).encode())


def bootstrap_table(
    pg_conn: Connection,
    schema: str,
    table_name: str,
    rls: list[UnitMapping] | None,
    claim: str | None,
) -> None:
    """Apply table grants and optional row-level security."""
    statements = [
        load_template(
            TemplateSpec(
                path="pg/grant_select",
                mapping={
                    "schema": Identifier(schema),
                    "table": Identifier(table_name),
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                },
            )
        )
    ]

    match rls:
        case list():
            if claim is None:
                message = f"Schema {schema} has no configured identity claim for RLS"
                raise RuntimeError(message)
            statements.append(
                table_access_policy_statement(schema, table_name, rls, claim)
            )
        case None:
            statements.append(schema_scope_statement(schema, table_name))
        case _:
            assert_never(rls)

    pg_conn.execute(";".join(statements).encode())
