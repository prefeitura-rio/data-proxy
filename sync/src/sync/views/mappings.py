"""Column and mapping helpers for source view generation."""

from typing import TYPE_CHECKING, Final

from psycopg.sql import Identifier, Literal

from sync.constants import (
    AGGREGATE_FUNCTION_SUFFIX,
    DUCKDB_VIEW_PREFIX,
    MAX_IDENTIFIER_BYTES,
)
from sync.executor import Executor
from sync.models import PartitionedTable, TableConfig, UnitMapping
from sync.schema import schema as app_schema
from sync.settings import settings
from sync.sources.registry import sources
from sync.templates import render_template
from sync.utils import json_digest

if TYPE_CHECKING:
    from psycopg.rows import TupleRow

    from sync.postgres import Postgres
    from sync.sources.source import Source
    from sync.types import PostgresParams, TemplateValue


def is_nested_or_json(duckdb_type: str) -> bool:
    """Return true when a DuckDB type carries JSON, alone or nested."""
    upper = duckdb_type.upper()
    return upper.startswith(("STRUCT", "ARRAY", "LIST", "JSON"))


def pg_scalar_type(duckdb_type: str) -> str:
    """Return the PostgreSQL scalar type for one DuckDB column type."""
    match duckdb_type.upper():
        case "DATE" | "BOOLEAN" as t:
            return t.lower()
        case t if t.startswith(("INTEGER", "BIGINT", "INT")):
            return "bigint"
        case _:
            return "text"


NUMERIC_TYPES: Final = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "INT",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
    }
)


def is_numeric(duckdb_type: str) -> bool:
    """Return true when DuckDB can sum and average the type.

    PostgreSQL receives a decimal or a floating point value as text, so it cannot sum or average the column.
    DuckDB can, so the aggregate function runs the sum and the average there.
    """
    return duckdb_type.upper().split("(")[0].strip() in NUMERIC_TYPES


GROUPABLE_TYPES: Final = frozenset(
    {"VARCHAR", "TEXT", "STRING", "BIGINT", "INTEGER", "INT", "DATE", "BOOLEAN"}
)


def is_groupable(duckdb_type: str) -> bool:
    """Return true when a group by on the column gives the same value as the table function.

    The column reaches PostgreSQL as text, a whole number, a date, or a flag. Another type reaches it as text,
    so DuckDB would give a number or a timestamp where the table gives a text. Those columns use the table.
    """
    return duckdb_type.upper() in GROUPABLE_TYPES


def is_sortable(duckdb_type: str) -> bool:
    """Return true when DuckDB and PostgreSQL sort the type the same way.

    Every other scalar type reaches PostgreSQL as text, which sorts by the collation.
    """
    return duckdb_type.upper() in {"BIGINT", "INTEGER", "INT", "DATE", "BOOLEAN"}


def changes_function_name(table: TableConfig) -> str:
    """Return the name of the change-feed function of one table, as PostgreSQL stores it.

    PostgreSQL cuts an identifier at 63 bytes without an error. The reconcile compares this name with
    the stored one, so it must cut the same way.
    """
    return f"dl_changes_{table.table_name}".encode()[:MAX_IDENTIFIER_BYTES].decode(
        errors="ignore"
    )


def aggregate_function_name(table_name: str) -> str | None:
    """Return the name of the aggregate function of one table, or None if the name is too long.

    PostgreSQL cuts an identifier at 63 bytes without an error, and the cut names of two tables can match.
    The proxy does not call the function of a table whose name is too long, and the table function answers.
    """
    name = f"{table_name}{AGGREGATE_FUNCTION_SUFFIX}"
    return name if len(name.encode()) <= MAX_IDENTIFIER_BYTES else None


def quoted_identifier(identifier: str) -> str:
    """Quote an identifier for PostgreSQL and DuckDB SQL."""
    return Identifier(identifier).as_string(None)


def return_type_for(duckdb_type: str) -> str:
    """Return the PostgreSQL type for one DuckDB column."""
    if is_nested_or_json(duckdb_type):
        return "text"
    return pg_scalar_type(duckdb_type)


def function_columns(
    columns: list[tuple[str, str]], *, raw_json: bool
) -> list[dict[str, TemplateValue]]:
    """Build the column metadata used by query-function templates."""
    return [
        {
            "name": Identifier(column).as_string(None),
            "key": Literal(column).as_string(None),
            "is_json": is_nested_or_json(duckdb_type),
            "raw_json": raw_json,
            "pg_type": pg_scalar_type(duckdb_type),
            "return_type": return_type_for(duckdb_type),
            "sortable": is_sortable(duckdb_type),
            "numeric": is_numeric(duckdb_type),
            "groupable": is_groupable(duckdb_type),
        }
        for column, duckdb_type in columns
    ]


async def column_types_from_duckdb(
    pg_conn: Postgres, table: TableConfig
) -> list[tuple[str, str]]:
    """Return column names and DuckDB types for the configured source table."""
    source = sources.configure(table.resolved_source, table.resolved_source_settings)

    executor: Executor[PostgresParams, list[TupleRow]] = Executor(conn=pg_conn)
    try:
        await executor.execute(
            "postgres/load_source", mapping={"load": Literal(f"{source.load};")}
        )

        rows = await executor.query(
            "postgres/describe_source",
            mapping={"source": source.scan(table.name)},
            expect=tuple[str, str],
        )
        return [(row[0], row[1]) for row in rows]
    finally:
        await source.close()


def ducklake_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Return mappings for the private DuckLake helper template."""
    table_name = table.table_name
    return {
        "schema": Identifier(schema),
        "app_schema": app_schema(),
        "function": Identifier(f"{table_name}_dl_fn"),
        "columns": function_columns(columns, raw_json=True),
        "duckdb_view": f"dl_{schema}_{table_name}",
        "source": f'"dl_{schema}".{quoted_identifier(table_name)}',
        "metadata_schema": schema,
        "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
    }


def source_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    source: Source,
) -> dict[str, TemplateValue]:
    """Return mappings for one private source helper template."""
    table_name = table.table_name
    fallback = source.fallback
    if fallback is None:  # pragma: no cover
        raise RuntimeError(f"Source {source.name!r} has no fallback helper")
    return {
        "schema": Identifier(schema),
        "app_schema": app_schema(),
        "function": Identifier(f"{table_name}_{fallback.suffix}"),
        "columns": function_columns(columns, raw_json=False),
        "duckdb_view": f"{DUCKDB_VIEW_PREFIX}{schema}_{table_name}",
        "load": source.load,
        "source": source.scan(table.name).as_string(None).replace("'", "''"),
    }


def serving_definition_signature(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> str:
    """Return the hash of the generated serving PostgreSQL definitions."""
    definitions = [
        render_template(
            "postgres/views/ducklake", ducklake_function_mapping(schema, table, columns)
        ),
        render_template(
            "postgres/views/create_function",
            table_function_mapping(schema, table, columns, claim),
        ),
        render_template(
            "postgres/views/create_view",
            boundary_view_mapping(
                schema, table.table_name, f"{table.table_name}_fn", columns
            ),
        ),
        render_template(
            "postgres/create_table_changes_function",
            table_changes_function_mapping(schema, table, columns, claim),
        ),
    ]
    aggregate_mapping = aggregate_function_mapping(schema, table, columns, claim)
    if aggregate_mapping is not None:
        definitions.append(
            render_template(
                "postgres/views/create_aggregate_function", aggregate_mapping
            )
        )
    source = sources.configure(table.resolved_source, table.resolved_source_settings)
    if isinstance(table, PartitionedTable) and table.fallback:
        fallback = source.fallback
        if fallback is None:  # pragma: no cover
            raise RuntimeError(f"Source {source.name!r} has no fallback helper")
        definitions.append(
            render_template(
                fallback.template,
                source_function_mapping(schema, table, columns, source),
            )
        )
    return json_digest(definitions)


def boundary_view_mapping(
    schema: str,
    view_name: str,
    function_name: str,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Build mappings for a PostgreSQL boundary view."""
    columns_sql = [
        (
            f"{quoted_identifier(column)}::jsonb AS {quoted_identifier(column)}"
            if is_nested_or_json(duckdb_type)
            else quoted_identifier(column)
        )
        for column, duckdb_type in columns
    ]
    return {
        "schema": Identifier(schema),
        "view": Identifier(view_name),
        "function": Identifier(function_name),
        "columns": columns_sql,
    }


def table_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> dict[str, TemplateValue]:
    """Return mappings for the function behind one table view."""
    table_name = table.table_name
    claim_name = claim if claim is not None and len(claim) > 0 else "sub"
    rls: list[UnitMapping] = [] if table.rls is None else table.rls

    source = sources.configure(table.resolved_source, table.resolved_source_settings)

    fallback: list[dict[str, TemplateValue]] = []
    if isinstance(table, PartitionedTable) and table.fallback:
        fallback_helper = source.fallback
        if fallback_helper is None:  # pragma: no cover
            raise RuntimeError(f"Source {source.name!r} has no fallback helper")
        fallback = [
            {
                "name": source.name,
                "function": Identifier(f"{table_name}_{fallback_helper.suffix}"),
                "view": f"{DUCKDB_VIEW_PREFIX}{schema}_{table_name}",
            }
        ]
    return {
        "schema": Identifier(schema),
        "schema_literal": Literal(schema),
        "app_schema": app_schema(),
        "function": Identifier(f"{table_name}_fn"),
        "dl_function": Identifier(f"{table_name}_dl_fn"),
        "dl_view": f"dl_{schema}_{table_name}",
        "columns": function_columns(columns, raw_json=True),
        "claim_setting": f"app.claim_{claim_name}",
        "has_rls": "true" if len(rls) > 0 else "false",
        "rls_mappings": [
            {"column": mapping.column, "unit_type": mapping.unit_type}
            for mapping in rls
        ],
        "source_table": Literal(table.name),
        "fallbacks": fallback,
        "user_role": Identifier(settings.AUTH_USER_ROLE),
    }


def aggregate_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> dict[str, TemplateValue] | None:
    """Return mappings for the aggregate function of one table, or None if the table has none."""
    name = aggregate_function_name(table.table_name)
    if name is None:
        return None
    return table_function_mapping(schema, table, columns, claim) | {
        "function": Identifier(name),
        "max_rows": str(settings.AGGREGATE_MAX_ROWS),
    }


def table_changes_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> dict[str, TemplateValue]:
    """Return mappings for one DuckLake table change-feed function."""
    claim_name = claim if claim is not None and len(claim) > 0 else "sub"
    rls: list[UnitMapping] = [] if table.rls is None else table.rls
    return {
        "schema": Identifier(schema),
        "schema_literal": Literal(schema),
        "function": Identifier(changes_function_name(table)),
        "table_name": table.table_name,
        "duckdb_view": f"dl_changes_{schema}_{table.table_name}",
        "columns": function_columns(columns, raw_json=True),
        "claim_setting": f"app.claim_{claim_name}",
        "has_rls": "true" if len(rls) > 0 else "false",
        "rls_mappings": [
            {"column": mapping.column, "unit_type": mapping.unit_type}
            for mapping in rls
        ],
        "metadata_schema": schema,
        "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        "user_role": Identifier(settings.AUTH_USER_ROLE),
    }
