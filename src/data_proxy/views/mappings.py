"""Column and mapping helpers for source view generation."""

from hashlib import sha256
from json import dumps

from psycopg.rows import TupleRow
from psycopg.sql import Identifier, Literal

from ..catalog import CatalogPaths
from ..constants import DUCKDB_VIEW_PREFIX
from ..executor import Executor
from ..models import PartitionedTable, TableConfig
from ..postgres import Postgres
from ..schema import schema as app_schema
from ..settings import settings
from ..sources.registry import sources
from ..sources.source import Source
from ..templates import render_template
from ..types import PostgresParams, TemplateValue


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
        "duckdb_view": f"ducklake_{schema}_{table_name}",
        "source": f"dl.{quoted_identifier(table_name)}",
        "catalog_local_path": str(CatalogPaths.for_schema(schema).local),
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
    return sha256(dumps(definitions, sort_keys=True).encode()).hexdigest()


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
    claim_name = claim or "sub"

    source = sources.configure(table.resolved_source, table.resolved_source_settings)

    fallback = []
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
        "app_schema": app_schema(),
        "function": Identifier(f"{table_name}_fn"),
        "dl_function": Identifier(f"{table_name}_dl_fn"),
        "dl_view": f"ducklake_{schema}_{table_name}",
        "columns": function_columns(columns, raw_json=True),
        "claim_setting": f"app.claim_{claim_name}",
        "has_rls": "true" if table.rls else "false",
        "rls_mappings": [
            {"column": str(mapping.column), "unit_type": str(mapping.unit_type)}
            for mapping in (table.rls or [])
        ],
        "source_table": Literal(table.name),
        "fallbacks": fallback,
        "user_role": Identifier(settings.AUTH_USER_ROLE),
    }


def table_changes_function_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> dict[str, TemplateValue]:
    """Return mappings for one DuckLake table change-feed function."""
    claim_name = claim or "sub"
    return {
        "schema": Identifier(schema),
        "function": Identifier(f"ducklake_changes_{table.table_name}"),
        "table_name": table.table_name,
        "duckdb_view": f"ducklake_changes_{schema}_{table.table_name}",
        "columns": function_columns(columns, raw_json=True),
        "claim_setting": f"app.claim_{claim_name}",
        "has_rls": "true" if table.rls else "false",
        "rls_mappings": [
            {"column": str(mapping.column), "unit_type": str(mapping.unit_type)}
            for mapping in (table.rls or [])
        ],
        "catalog_local_path": str(CatalogPaths.for_schema(schema).local),
        "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        "user_role": Identifier(settings.AUTH_USER_ROLE),
    }
