"""Install the serving functions and the access policy, and call them."""

import json

from psycopg.rows import TupleRow
from psycopg.sql import SQL, Composable, Identifier, Literal
from pydantic import TypeAdapter

from sync.executor import Executor
from sync.settings import settings
from sync.types import (
    DatabaseRow,
    PostgresParams,
)
from tests.constants import (
    PROXY_FILTER_CASES,
    ROUTED_TABLE,
)
from tests.fixtures.types import FilterCase, Postgres
from tests.helpers.core import (
    execute_sql,
    fetch_all,
    helm_sql,
    response_headers,
    scalar,
    set_request_headers,
    set_setting,
)


def schema_scope_condition(schema: str) -> Composable:
    """Return the schema-claim condition that the test policies use, as the provision job renders it."""
    return SQL("rbac.mine({})").format(Literal(schema))


async def render_filter_node(
    pg: Postgres, node: dict[str, object], columns: list[str]
) -> str | None:
    """Render one node of a filter tree with the columns that the table has."""
    rendered = await scalar(
        pg,
        "SELECT data_proxy.render_filter_node(%s::jsonb, %s::text[])",
        json.dumps(node),
        columns,
    )
    assert rendered is None or isinstance(rendered, str)
    return rendered


async def create_access_policy(pg: Postgres) -> None:
    """Create the production access_policy and access_log tables in the test schema."""
    await pg.connection.execute(
        helm_sql(
            "setup_access_policy",
            {
                "schema": pg.namespace.identifier,
                "user_role": Identifier(settings.AUTH_USER_ROLE),
                "scope": schema_scope_condition(pg.namespace.schema),
                "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
            },
        ).encode()
    )


async def insert_access_policy(
    pg: Postgres, subject: str, unit_type: str, unit_id: str
) -> None:
    """Insert one access-policy grant in the test schema."""
    await execute_sql(
        pg,
        "postgres/insert_access_policy",
        mapping={"schema": pg.namespace.identifier},
        params={"subject": subject, "unit_type": unit_type, "unit_id": unit_id},
    )


async def grant_unit(pg: Postgres, subject: str) -> None:
    """Act as the subject and grant it the unit u1."""
    await set_setting(pg, "app.claim_sub", subject)
    await insert_access_policy(pg, subject, "unit", "u1")


async def install_table_function(
    pg: Postgres, *, has_rls: bool, fallbacks: list[str]
) -> None:
    """Render the per-table function over the stub source helpers."""
    from sync.views.mappings import function_columns

    columns = function_columns(
        [("source", "VARCHAR"), ("arg1", "VARCHAR"), ("arg2", "VARCHAR")],
        raw_json=True,
    )
    await Executor[PostgresParams, list[TupleRow]](conn=pg.backend).execute(
        "postgres/views/create_function",
        mapping={
            "schema": pg.namespace.identifier,
            "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
            "function": Identifier("t_fn"),
            "dl_function": Identifier("t_dl_fn"),
            "dl_view": "dl_t",
            "columns": columns,
            "claim_setting": "app.claim_sub",
            "has_rls": str(has_rls).lower(),
            "rls_mappings": [{"column": "unit_id", "unit_type": "unit"}],
            "source_table": Literal(ROUTED_TABLE),
            "fallbacks": [
                {
                    "name": name,
                    "function": Identifier(
                        f"t_{'bq_fn' if name == 'bigquery' else name}"
                    ),
                    "view": "source_t",
                }
                for name in fallbacks
            ],
            "user_role": Identifier(settings.AUTH_USER_ROLE),
            "schema_literal": Literal(pg.namespace.schema),
        },
    )


async def install_aggregate_function(
    pg: Postgres,
    *,
    has_rls: bool,
    fallbacks: list[str],
    extra_columns: int = 0,
    max_rows: int = 1000,
) -> None:
    """Render the aggregate function over the stub DuckLake helper.

    The extra columns exist only in the metadata of the function, to test a table with many columns.
    """
    from sync.views.mappings import function_columns

    columns = function_columns(
        [
            ("unit_id", "VARCHAR"),
            ("grp", "VARCHAR"),
            ("qty", "BIGINT"),
            ("day", "DATE"),
            ("flag", "BOOLEAN"),
            ("tags", "JSON"),
            ("ratio", "DOUBLE"),
            ("label", "VARCHAR"),
            *[(f"extra_{index}", "BIGINT") for index in range(extra_columns)],
        ],
        raw_json=True,
    )
    await Executor[PostgresParams, list[TupleRow]](conn=pg.backend).execute(
        "postgres/views/create_aggregate_function",
        mapping={
            "schema": pg.namespace.identifier,
            "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
            "function": Identifier("t_agg"),
            "dl_function": Identifier("t_dl_fn"),
            "dl_view": "dl_t",
            "columns": columns,
            "claim_setting": "app.claim_sub",
            "has_rls": str(has_rls).lower(),
            "rls_mappings": [{"column": "unit_id", "unit_type": "unit"}],
            "source_table": Literal(ROUTED_TABLE),
            "fallbacks": [{"name": name} for name in fallbacks],
            "user_role": Identifier(settings.AUTH_USER_ROLE),
            "schema_literal": Literal(pg.namespace.schema),
            "max_rows": str(max_rows),
        },
    )


async def response_status(pg: Postgres) -> str | None:
    """Return the status that the function asked PostgREST to send, or None if it asked for none."""
    raw = await scalar(pg, "SELECT current_setting('response.status', true)")
    return None if raw is None or len(str(raw)) == 0 else str(raw)


async def call_aggregate_function(
    pg: Postgres,
    document: dict[str, object] | None,
    role: str = "dp_api",
    schemas: str | None = None,
) -> tuple[object, dict[str, str]]:
    """Call the aggregate function as a token, with the filter header of the document.

    The first value is the parsed answer. It is None when the function answers null.
    """
    await act_as_token(pg, role, pg.namespace.schema if schemas is None else schemas)
    if document is not None:
        await set_request_headers(pg, {"x-ducklake-filter": json.dumps(document)})
    raw = await scalar(pg, f"SELECT {pg.namespace.schema}.t_agg()::text")
    answer = None if raw is None else json.loads(str(raw))
    return answer, await response_headers(pg)


async def act_as_token(pg: Postgres, role: str, schemas: str) -> None:
    """Give the connection the permissions of one role and the schemas claim of a token.

    The settings last for the whole session, so they survive a commit. rbac.load()
    sets them for one transaction, as the pre-request function does for a request.
    """
    await pg.connection.execute(
        """
        SELECT
            set_config(
                'rbac.permissions',
                coalesce((SELECT wrapped FROM rbac.roles WHERE name = %s), ''),
                false
            ),
            set_config('rbac.schemas', ',' || %s || ',', false)
        """,
        (role, schemas),
    )


async def call_table_function(
    pg: Postgres, role: str = "dp_api", schemas: str | None = None
) -> tuple[list[DatabaseRow], dict[str, str]]:
    """Call the per-table function as a token with one role and, by default, the test schema in its claim."""
    await act_as_token(pg, role, pg.namespace.schema if schemas is None else schemas)
    rows = await fetch_all(
        pg,
        "postgres/select_table_function",
        mapping={"schema": pg.namespace.identifier},
    )
    return rows, await response_headers(pg)


def proxy_filter_cases() -> list[FilterCase]:
    """Read the cases that the Lua parser specs share with the PostgreSQL filter tests."""
    return TypeAdapter(list[FilterCase]).validate_json(PROXY_FILTER_CASES.read_text())


type GroupColumn = str | tuple[str, str]


def aggregate(
    *items: dict[str, str],
    group: list[GroupColumn] | None = None,
    order: list[dict[str, object]] | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, object]:
    """Build the filter document of an aggregate.

    A group column is a name, or a pair of the column and the alias.
    """
    body: dict[str, object] = {"items": list(items)}
    if group is not None:
        body["group"] = [
            {"column": entry, "alias": entry}
            if isinstance(entry, str)
            else {"column": entry[0], "alias": entry[1]}
            for entry in group
        ]
    tail = {"order": order, "limit": limit, "offset": offset}
    body.update({name: value for name, value in tail.items() if value is not None})
    return {"aggregate": body}


def by(
    column: str, *, descending: bool = False, nulls: str | None = None
) -> dict[str, object]:
    """Build one order entry."""
    entry: dict[str, object] = {"column": column, "descending": descending}
    if nulls is not None:
        entry["nulls"] = nulls
    return entry


def comparison(
    column: str, operator: str, value: object, *, negated: bool = False
) -> dict[str, object]:
    """Build one comparison node of a filter tree."""
    return {
        "kind": "comparison",
        "column": column,
        "operator": operator,
        "value": value,
        "negated": negated,
    }


def group(
    operator: str, *items: dict[str, object], negated: bool = False
) -> dict[str, object]:
    """Build one group node of a filter tree."""
    return {
        "kind": "group",
        "operator": operator,
        "items": list(items),
        "negated": negated,
    }


def total(alias: str = "n") -> dict[str, str]:
    """Build the aggregate item sum(qty)."""
    return {"fn": "sum", "column": "qty", "alias": alias}


def ordered(rows: object) -> list[dict[str, object]]:
    """Sort the rows by their text, because the function gives the groups in no order."""
    assert isinstance(rows, list)
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


async def add_files_function(pg: Postgres, schema: str) -> None:
    """Add a stand-in for the function that lists the files of a table, which the sync worker makes."""
    await pg.connection.execute(
        f"""
        CREATE FUNCTION {schema}.dl_table_files(table_name text)
        RETURNS TABLE (path text, size_bytes bigint, delete_file text)
        LANGUAGE sql AS $$ SELECT table_name || '.parquet', 10::bigint, NULL::text $$
        """.encode()
    )
