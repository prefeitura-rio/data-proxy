"""Column and mapping helpers for source view generation."""

import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

from psycopg.sql import Identifier, Literal

from sync.constants import (
    DUCKDB_VIEW_PREFIX,
)
from sync.ducklake import data_path
from sync.executor import Executor
from sync.models import PartitionedTable, RlsRule, TableConfig, UnitMapping
from sync.schema import schema as app_schema
from sync.settings import settings
from sync.sources.registry import sources
from sync.templates import render_template
from sync.types import DescribeRow
from sync.utils import json_digest

if TYPE_CHECKING:
    from psycopg.rows import TupleRow

    from sync.postgres import Postgres
    from sync.sources.source import Source
    from sync.types import PostgresParams, TemplateValue


def is_nested_or_json(duckdb_type: str) -> bool:
    """Return true when a DuckDB type is JSON or holds nested values: a struct, a map, a union, or a list.

    DuckDB names a list `BIGINT[]` and a fixed-size array `INTEGER[3]`, so a type that ends in `]` is nested too.
    """
    upper = duckdb_type.strip().upper()
    return upper.startswith(("STRUCT", "MAP", "UNION", "ARRAY", "LIST", "JSON")) or (
        upper.endswith("]")
    )


def is_list(duckdb_type: str) -> bool:
    """Return true when the DuckDB type is a list, such as `VARCHAR[]`."""
    return duckdb_type.endswith("[]")


def flatten_rules(rls: list[RlsRule]) -> list[tuple[UnitMapping, str]]:
    """Return each condition with the number of its group, or the empty text for a rule of one condition.

    The number is the position of the group among the groups, from 1. Only the template reads it.
    """
    flat: list[tuple[UnitMapping, str]] = []
    number = 0

    for rule in rls:
        if len(rule.conditions) > 1:
            number += 1
            flat.extend((condition, str(number)) for condition in rule.conditions)
        else:
            flat.append((rule.conditions[0], ""))

    return flat


def rls_mapping_entries(
    rls: list[RlsRule], columns: list[tuple[str, str]]
) -> list[dict[str, TemplateValue]]:
    """Return the rls mappings with the DuckDB type of each column, which the macro uses to choose the predicate.

    A list column matches a grant when one of its values equals the unit id, so only a list of text can work.
    """
    types = dict(columns)
    entries: list[dict[str, TemplateValue]] = []

    for mapping, group in flatten_rules(rls):
        if mapping.column not in types:
            raise ValueError(
                f"The rls column {mapping.column!r} is not a column of the source table"
            )

        duckdb_type = types[mapping.column]

        if is_list(duckdb_type) and duckdb_type.upper() != "VARCHAR[]":
            raise ValueError(
                f"Column {mapping.column!r} has type {duckdb_type}, but a list column with an rls rule must be VARCHAR[]"
            )

        entries.append(
            {
                "column": mapping.column,
                "unit_type": mapping.unit_type,
                "duckdb_type": duckdb_type.upper(),
                "group": group,
            }
        )

    return entries


def rls_mappings_json(
    entries: Sequence[Mapping[str, TemplateValue]],
) -> list[dict[str, str | int | None]]:
    """Return the unit mappings in the shape that `rls_where` reads: the column, the unit type, the type and the group.

    The table function and the generic functions both pass this list, so the row policy has one input and one function.
    """
    return [
        {
            "col": str(entry["column"]),
            "ut": str(entry["unit_type"]),
            "ty": str(entry["duckdb_type"]),
            "grp": None if entry["group"] == "" else int(str(entry["group"])),
        }
        for entry in entries
    ]


WHOLE_NUMBER_TYPES: Final = frozenset({"INTEGER", "INT", "BIGINT"})
FLOATING_POINT_TYPES: Final = frozenset({"DOUBLE", "FLOAT", "REAL"})
TIMESTAMP_TYPES: Final = frozenset({"TIMESTAMP", "TIMESTAMP_MS", "TIMESTAMP_S"})
TIMESTAMP_TZ_TYPES: Final = frozenset({"TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"})


def pg_scalar_type(duckdb_type: str) -> str:
    """Return the PostgreSQL scalar type for one DuckDB column type.

    A decimal and a floating point value keep a numeric type, so PostgreSQL compares, sums and orders them as numbers.
    A timestamp keeps a timestamp type, so PostgreSQL orders it by time and not by the text of the time.
    A nanosecond timestamp stays text, because PostgreSQL keeps microseconds and a cast would merge two values
    that differ in the last three digits.
    """
    upper = duckdb_type.upper()
    base = upper.split("(")[0].strip()
    match base:
        case "DATE" | "BOOLEAN":
            return base.lower()
        case t if t in WHOLE_NUMBER_TYPES:
            return "bigint"
        case "DECIMAL" | "NUMERIC":
            return "numeric"
        case t if t in FLOATING_POINT_TYPES:
            return "double precision"
        case t if t in TIMESTAMP_TYPES:
            return "timestamp"
        case t if t in TIMESTAMP_TZ_TYPES:
            return "timestamptz"
        case _:
            return "text"


EXACT_NUMERIC_TYPES: Final = frozenset(
    {"TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "DECIMAL"}
)
ORDERED_TYPES: Final = frozenset(
    {
        *EXACT_NUMERIC_TYPES,
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DATE",
        "TIMESTAMP",
        "TIMESTAMP_MS",
        "TIMESTAMP_S",
    }
)


def is_exact_numeric(duckdb_type: str) -> bool:
    """Return true when DuckDB sums the type to the same digits that PostgreSQL gives.

    A whole number and a decimal sum exactly in both engines, so DuckDB runs the sum, and the average as a sum
    divided by a count. A double and a float differ in the last digits, because the sum depends on the order of the
    rows and on the version of DuckDB. A hugeint and a decimal of 20 digits or more overflow in DuckDB where
    PostgreSQL keeps going. Those columns use the table, which PostgreSQL sums.
    """
    base = duckdb_type.upper().split("(")[0].strip()
    if base not in EXACT_NUMERIC_TYPES:
        return False
    match = re.search(r"\((\d+)", duckdb_type)
    return base != "DECIMAL" or match is None or int(match.group(1)) <= MAX_EXACT_DIGITS


MAX_EXACT_DIGITS: Final = 18


def is_ordered(duckdb_type: str) -> bool:
    """Return true when min and max of the type give the value that PostgreSQL gives.

    A number, a date and a timestamp compare the same way in both engines. Text compares by bytes in DuckDB and by the
    collation in PostgreSQL, and a nanosecond timestamp reaches PostgreSQL as text. A timestamp with a time zone is out,
    because DuckDB and PostgreSQL write the offset in different forms.
    """
    base = duckdb_type.upper().split("(")[0].strip()
    return base in ORDERED_TYPES


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


LTREE_LABEL = re.compile(r"[A-Za-z0-9_-]+")


def scope_path(schema: str, table: str | None = None) -> str:
    """Return the ltree path of a schema or of one of its tables, as `<project>.<schema>[.<table>]`.

    A name that ltree can't hold as a label gives the path of the schema, so a grant on the schema still covers
    the table and no grant can name the table alone.
    """
    path = schema.replace("__", ".", 1)
    if table is not None and LTREE_LABEL.fullmatch(table) is not None:
        return f"{path}.{table}"

    return path


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
            "exact_numeric": is_exact_numeric(duckdb_type),
            "ordered": is_ordered(duckdb_type),
            "groupable": is_groupable(duckdb_type),
            "countable": not is_nested_or_json(duckdb_type),
            "timestamp": pg_scalar_type(duckdb_type) == "timestamp",
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
            expect=DescribeRow,
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
        "data_path": data_path(schema),
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
            "postgres/write_meta",
            meta_mapping(schema, table, columns, claim),
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
    return json_digest(definitions)


def boundary_view_mapping(
    schema: str,
    view_name: str,
    function_name: str,
    columns: list[tuple[str, str]],
) -> dict[str, TemplateValue]:
    """Build mappings for a PostgreSQL boundary view.

    The table function returns JSON and nested columns as text, because pg_duckdb pushes a cast to jsonb into
    DuckDB, which has no such type. The view converts that text, so a client and a JSON path see jsonb.
    """
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
    rls: list[RlsRule] = [] if table.rls is None else table.rls

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
        "scope_path": Literal(scope_path(schema, table_name)),
        "app_schema": app_schema(),
        "function": Identifier(f"{table_name}_fn"),
        "dl_function": Identifier(f"{table_name}_dl_fn"),
        "duckdb_view": f"dl_{schema}_{table_name}",
        "columns": function_columns(columns, raw_json=True),
        "claim_setting": f"app.claim_{claim_name}",
        "rls_mappings": Literal(
            json.dumps(rls_mappings_json(rls_mapping_entries(rls, columns)))
        ),
        "source_table": Literal(table.name),
        "fallbacks": fallback,
        "user_role": Identifier(settings.AUTH_USER_ROLE),
    }


def meta_columns(columns: list[tuple[str, str]]) -> list[dict[str, str | bool]]:
    """Return the columns for `meta`: the name as PostgreSQL stores it, the type, and what each column allows."""
    return [
        {
            "name": column,
            "pg_type": pg_scalar_type(duckdb_type),
            "groupable": is_groupable(duckdb_type),
            "countable": not is_nested_or_json(duckdb_type),
            "exact_numeric": is_exact_numeric(duckdb_type),
            "ordered": is_ordered(duckdb_type),
            "timestamp": pg_scalar_type(duckdb_type) == "timestamp",
        }
        for column, duckdb_type in columns
    ]


def meta_mapping(
    schema: str,
    table: TableConfig,
    columns: list[tuple[str, str]],
    claim: str | None,
) -> dict[str, TemplateValue]:
    """Return the mapping of the row of `meta`, the facts that the generic functions read when they run."""
    table_name = table.table_name
    claim_name = claim if claim is not None and len(claim) > 0 else "sub"
    rls: list[RlsRule] = [] if table.rls is None else table.rls
    source = sources.configure(table.resolved_source, table.resolved_source_settings)
    fallbacks: list[dict[str, str]] = (
        [{"name": source.name}]
        if isinstance(table, PartitionedTable) and table.fallback
        else []
    )
    mappings = rls_mappings_json(rls_mapping_entries(rls, columns))
    return {
        "app_schema": app_schema(),
        "schema_name": Literal(schema),
        "table_name": Literal(table_name),
        "source_table": Literal(table.name),
        "scope_path": Literal(scope_path(schema, table_name)),
        "claim_setting": Literal(f"app.claim_{claim_name}"),
        "data_path": Literal(data_path(schema)),
        "duckdb_view": Literal(f"dl_{schema}_{table_name}"),
        "max_rows": str(settings.AGGREGATE_MAX_ROWS),
        "columns": Literal(json.dumps(meta_columns(columns))),
        "rls_mappings": Literal(json.dumps(mappings)),
        "fallbacks": Literal(json.dumps(fallbacks)),
    }
