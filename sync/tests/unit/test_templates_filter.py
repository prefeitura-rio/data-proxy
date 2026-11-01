"""Tests for the templates that render the filter of a request."""

import json
import re
from typing import Final

import psycopg
import pytest
from psycopg.sql import Identifier

from sync.models import (
    Strategy,
    TableState,
)
from sync.settings import settings
from sync.templates import render_template
from sync.views.reconcile import ROUTING_TEMPLATES
from tests.constants import (
    ROUTED_TABLE,
)
from tests.fixtures.types import Postgres
from tests.helpers.core import execute_sql, scalar, set_request_headers, set_setting
from tests.helpers.routing import (
    aggregate,
    call_table_function,
    comparison,
    group,
    install_table_function,
    proxy_filter_cases,
    render_filter_node,
)
from tests.helpers.state import put_state

FULL: Final = TableState(strategy=Strategy.FULL, signature="s")


PARSED_FILTERS: Final = [
    pytest.param(json.dumps({"where": case["expected"]["where"]}), id=case["name"])
    for case in proxy_filter_cases()
    if case["expected"] is not None and "where" in case["expected"]
]


@pytest.mark.postgres
class TestFilterFunctions:
    """The functions that render the filter of a request for DuckDB, and the pushdown of the columns and the limit."""

    async def _install_filter(self, postgres: Postgres) -> None:
        """Install the routing filter function under test."""
        for template in ("quoting", "filter"):
            await postgres.connection.execute(
                render_template(
                    f"postgres/views/routing/{template}",
                    {"schema": Identifier(settings.DBOS_APP_SCHEMA)},
                )
            )

    async def test_filter_renders_where_nodes(self, postgres: Postgres) -> None:
        """Renders where nodes."""
        await self._install_filter(postgres)

        cases = [
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "gte",
                        "negated": False,
                        "value": "10",
                    }
                },
                "(\"id\" >= '10')",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "in",
                        "negated": False,
                        "value": ["1", "2"],
                    }
                },
                "(\"id\" IN ('1', '2'))",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "name",
                        "operator": "ilike",
                        "negated": False,
                        "value": "*rio*",
                    }
                },
                "(\"name\" ILIKE '%rio%')",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "eq",
                        "negated": True,
                        "value": "1",
                    }
                },
                "NOT ((\"id\" = '1'))",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "is",
                        "negated": False,
                        "value": "not_null",
                    }
                },
                '("id" IS NOT NULL)',
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "is",
                        "negated": False,
                        "value": "unknown",
                    }
                },
                '("id" IS UNKNOWN)',
            ),
            (
                {
                    "where": {
                        "kind": "group",
                        "operator": "or",
                        "items": [
                            {
                                "kind": "comparison",
                                "column": "id",
                                "operator": "eq",
                                "negated": False,
                                "value": "1",
                            },
                            {
                                "kind": "comparison",
                                "column": "name",
                                "operator": "eq",
                                "negated": False,
                                "value": "x",
                            },
                        ],
                    }
                },
                "((\"id\" = '1') OR (\"name\" = 'x'))",
            ),
        ]

        for payload, expected in cases:
            rendered = await scalar(
                postgres,
                "SELECT data_proxy.render_dl_where(%s, ARRAY['id', 'name']::text[])",
                json.dumps(payload),
            )
            assert rendered == expected

    @pytest.mark.parametrize("document", PARSED_FILTERS)
    async def test_filter_renders_every_document_that_the_proxy_parser_builds(
        self, postgres: Postgres, document: str
    ) -> None:
        """Renders each document in the proxy parser fixtures and quotes its columns."""
        await self._install_filter(postgres)
        columns = set(re.findall(r'"column": "([^"]+)"', document))

        rendered = await scalar(
            postgres,
            "SELECT data_proxy.render_dl_where(%s, %s::text[])",
            document,
            sorted(columns),
        )

        assert isinstance(rendered, str)
        assert {f'"{column}"' for column in columns} <= set(
            re.findall(r'"[^"]+"', rendered)
        )

    async def test_filter_rejects_unknown_columns_and_operators(
        self, postgres: Postgres
    ) -> None:
        """Rejects unknown columns and operators."""
        await self._install_filter(postgres)

        unknown_column = {
            "where": {
                "kind": "comparison",
                "column": "nope",
                "operator": "eq",
                "negated": False,
                "value": "1",
            }
        }
        with pytest.raises(psycopg.Error) as error:
            async with postgres.connection.transaction():
                await scalar(
                    postgres,
                    "SELECT data_proxy.render_dl_where(%s, ARRAY['id']::text[])",
                    json.dumps(unknown_column),
                )
        assert error.value.sqlstate == "PT400"

        unknown_operator = {
            "where": {
                "kind": "comparison",
                "column": "id",
                "operator": "fts",
                "negated": False,
                "value": "x",
            }
        }
        with pytest.raises(psycopg.Error) as error:
            async with postgres.connection.transaction():
                await scalar(
                    postgres,
                    "SELECT data_proxy.render_dl_where(%s, ARRAY['id']::text[])",
                    json.dumps(unknown_operator),
                )
        assert error.value.sqlstate == "PT400"

    async def test_filter_request_filter_reads_the_header(
        self, postgres: Postgres
    ) -> None:
        """Request filter reads the header."""
        await self._install_filter(postgres)

        assert await scalar(postgres, "SELECT data_proxy.request_filter()") is None

        payload = {
            "where": {
                "kind": "comparison",
                "column": "id",
                "operator": "eq",
                "negated": False,
                "value": "1",
            }
        }
        await set_setting(
            postgres,
            "request.headers",
            json.dumps({"x-ducklake-filter": json.dumps(payload)}),
        )
        assert await scalar(postgres, "SELECT data_proxy.request_filter()") == payload

        await set_setting(
            postgres, "request.headers", json.dumps({"x-ducklake-filter": "not-json"})
        )
        with pytest.raises(psycopg.Error) as error:
            await postgres.connection.execute("SELECT data_proxy.request_filter()")
        assert error.value.sqlstate == "PT400"

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param(None, None, id="no-document"),
            pytest.param({}, None, id="no-columns"),
            pytest.param({"columns": ["id"]}, ["id"], id="one-column"),
            pytest.param(
                {"columns": ["name", "id"]}, ["name", "id"], id="keeps-the-order"
            ),
            pytest.param(
                {"columns": ["id", "nope"]}, ["id"], id="drops-an-unknown-column"
            ),
            pytest.param({"columns": ["nope"]}, [], id="only-unknown-columns"),
        ],
    )
    async def test_filter_renders_the_columns_that_the_table_has(
        self,
        postgres: Postgres,
        document: dict[str, object] | None,
        expected: object,
    ) -> None:
        """Keeps the requested names that are columns, and returns null when the request reads all."""
        await self._install_filter(postgres)

        rendered = await scalar(
            postgres,
            "SELECT data_proxy.render_dl_columns(%s::jsonb, ARRAY['id', 'name']::text[])",
            None if document is None else json.dumps(document),
        )

        assert rendered == expected

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param(None, None, id="no-document"),
            pytest.param({"columns": ["id"]}, None, id="no-limit"),
            pytest.param({"limit": 5}, "LIMIT 5", id="limit-only"),
            pytest.param({"limit": 0}, "LIMIT 0", id="limit-zero"),
            pytest.param(
                {"limit": 5, "order": [{"column": "id", "descending": False}]},
                'ORDER BY "id" ASC NULLS LAST LIMIT 5',
                id="ascending-puts-nulls-last-like-postgresql",
            ),
            pytest.param(
                {"limit": 5, "order": [{"column": "id", "descending": True}]},
                'ORDER BY "id" DESC NULLS FIRST LIMIT 5',
                id="descending-puts-nulls-first-like-postgresql",
            ),
            pytest.param(
                {
                    "limit": 3,
                    "order": [
                        {"column": "id", "descending": True, "nulls": "last"},
                        {"column": "day", "descending": False, "nulls": "first"},
                    ],
                },
                'ORDER BY "id" DESC NULLS LAST, "day" ASC NULLS FIRST LIMIT 3',
                id="several-columns-with-nulls",
            ),
            pytest.param(
                {"limit": 5, "order": [{"column": "name", "descending": False}]},
                None,
                id="text-order-is-not-pushed",
            ),
            pytest.param(
                {"limit": 5, "order": [{"column": "nope", "descending": False}]},
                None,
                id="unknown-order-column-is-not-pushed",
            ),
            pytest.param(
                {
                    "limit": 5,
                    "order": [
                        {"column": "id", "descending": False},
                        {"column": "name", "descending": False},
                    ],
                },
                None,
                id="one-text-order-column-stops-the-push",
            ),
            pytest.param(
                {"order": [{"column": "id", "descending": False}]},
                None,
                id="order-without-limit",
            ),
        ],
    )
    async def test_filter_renders_the_order_and_limit_that_the_database_can_push(
        self,
        postgres: Postgres,
        document: dict[str, object] | None,
        expected: object,
    ) -> None:
        """Pushes the limit with an order only when DuckDB sorts the same rows as PostgreSQL."""
        await self._install_filter(postgres)

        rendered = await scalar(
            postgres,
            "SELECT data_proxy.render_dl_tail(%s::jsonb, ARRAY['id', 'day']::text[])",
            None if document is None else json.dumps(document),
        )

        assert rendered == expected

    async def test_filter_rejects_a_negative_limit(self, postgres: Postgres) -> None:
        """Rejects a negative limit."""
        await self._install_filter(postgres)

        with pytest.raises(psycopg.Error) as error:
            await scalar(
                postgres,
                "SELECT data_proxy.render_dl_tail(%s::jsonb, ARRAY['id']::text[])",
                json.dumps({"limit": -1}),
            )

        assert error.value.sqlstate == "PT400"

    async def test_pushed_columns_and_limit_return_the_same_rows_as_the_full_read(
        self, postgres: Postgres
    ) -> None:
        """A column that the request skips comes back null, and the rest equals the full read."""
        await self._install_filter(postgres)
        await postgres.connection.execute(
            """
            SELECT duckdb.raw_query(
                'CREATE OR REPLACE VIEW pushed_source AS '
                || 'SELECT i::BIGINT AS id, ''n'' || i AS name, '
                || 'DATE ''2026-01-01'' + i::INTEGER AS day, i % 2 = 0 AS flag '
                || 'FROM range(1, 7) AS t(i)'
            )
            """
        )

        async def read(
            filter_document: dict[str, object] | None,
        ) -> list[tuple[object, ...]]:
            cursor = await postgres.connection.execute(
                """
                SELECT duckdb.raw_query(
                    'CREATE OR REPLACE VIEW pushed_view AS SELECT '
                    || data_proxy.project_columns(
                        ARRAY['id', 'name', 'day', 'flag']::text[],
                        data_proxy.render_dl_columns(
                            %(doc)s::jsonb, ARRAY['id', 'name', 'day', 'flag']::text[]
                        )
                    )
                    || ' FROM pushed_source '
                    || coalesce(
                        data_proxy.render_dl_tail(
                            %(doc)s::jsonb, ARRAY['id', 'day', 'flag']::text[]
                        ),
                        ''
                    )
                )
                """,
                {
                    "doc": None
                    if filter_document is None
                    else json.dumps(filter_document)
                },
            )
            await cursor.fetchall()
            cursor = await postgres.connection.execute(
                """
                SELECT r['id']::bigint, r['name']::text, r['day']::date, r['flag']::boolean
                FROM duckdb.query('SELECT id, name, day, flag FROM pushed_view') AS r
                ORDER BY 1
                """
            )
            return list(await cursor.fetchall())

        full = await read(None)
        projected = await read({"columns": ["id", "day"]})
        top = await read(
            {
                "columns": ["id"],
                "limit": 2,
                "order": [{"column": "id", "descending": True}],
            }
        )

        assert len(full) == 6
        assert [(row[0], row[2]) for row in projected] == [
            (row[0], row[2]) for row in full
        ]
        assert [(row[1], row[3]) for row in projected] == [(None, None)] * 6
        assert [row[0] for row in top] == [5, 6]
        assert [(row[1], row[2], row[3]) for row in top] == [(None, None, None)] * 2

    @pytest.mark.parametrize(
        ("document", "columns", "tail"),
        [
            pytest.param(None, "<all>", "<none>", id="no-document"),
            pytest.param(
                {"columns": ["source", "arg1"]},
                "source,arg1",
                "<none>",
                id="columns-only",
            ),
            pytest.param({"limit": 3}, "<all>", "LIMIT 3", id="limit-without-an-order"),
            pytest.param(
                {
                    "columns": ["source"],
                    "limit": 3,
                    "order": [{"column": "source", "descending": False}],
                },
                "source",
                "<none>",
                id="a-text-order-stops-the-limit",
            ),
        ],
    )
    async def test_view_function_passes_the_columns_and_the_tail_to_the_ducklake_helper(
        self,
        table_function: Postgres,
        document: dict[str, object] | None,
        columns: str,
        tail: str,
    ) -> None:
        """Passes the requested columns and the tail that DuckDB can run."""
        await install_table_function(table_function, has_rls=False, fallbacks=[])
        await put_state(table_function, ROUTED_TABLE, FULL)
        if document is not None:
            await set_request_headers(
                table_function, {"x-ducklake-filter": json.dumps(document)}
            )

        await call_table_function(table_function)

        assert [
            await scalar(
                table_function, "SELECT current_setting('test.columns', true)"
            ),
            await scalar(table_function, "SELECT current_setting('test.tail', true)"),
        ] == [columns, tail]


COLUMNS: Final = ["id", "name", 'a"b']


@pytest.mark.postgres
@pytest.mark.usefixtures("routing")
class TestRenderFilterNode:
    """Each node of the tree becomes one condition that DuckDB can run."""

    @pytest.mark.parametrize(
        ("node", "expected"),
        [
            pytest.param(comparison("id", "eq", "1"), "(\"id\" = '1')", id="eq"),
            pytest.param(comparison("id", "neq", "1"), "(\"id\" <> '1')", id="neq"),
            pytest.param(comparison("id", "gt", "1"), "(\"id\" > '1')", id="gt"),
            pytest.param(comparison("id", "gte", "1"), "(\"id\" >= '1')", id="gte"),
            pytest.param(comparison("id", "lt", "1"), "(\"id\" < '1')", id="lt"),
            pytest.param(comparison("id", "lte", "1"), "(\"id\" <= '1')", id="lte"),
            pytest.param(
                comparison("name", "like", "a*"), "(\"name\" LIKE 'a%')", id="like"
            ),
            pytest.param(
                comparison("name", "ilike", "*a*"),
                "(\"name\" ILIKE '%a%')",
                id="ilike",
            ),
            pytest.param(
                comparison("id", "in", ["1", "2"]),
                "(\"id\" IN ('1', '2'))",
                id="in-list",
            ),
            pytest.param(
                comparison("id", "is", "null"), '("id" IS NULL)', id="is-null"
            ),
            pytest.param(
                comparison("id", "is", "not_null"),
                '("id" IS NOT NULL)',
                id="is-not-null",
            ),
            pytest.param(
                comparison("id", "is", "true"), '("id" IS TRUE)', id="is-true"
            ),
            pytest.param(
                comparison("id", "is", "false"), '("id" IS FALSE)', id="is-false"
            ),
            pytest.param(
                comparison("id", "is", "unknown"),
                '("id" IS UNKNOWN)',
                id="is-unknown",
            ),
            pytest.param(
                comparison("id", "eq", "1", negated=True),
                "NOT ((\"id\" = '1'))",
                id="negated-comparison",
            ),
            pytest.param(
                comparison('a"b', "eq", "x"),
                '("a""b" = \'x\')',
                id="double-quote-in-a-column-name",
            ),
            pytest.param(
                comparison("name", "eq", "O'Brien"),
                "(\"name\" = 'O''Brien')",
                id="single-quote-in-a-value",
            ),
            pytest.param(
                group(
                    "and", comparison("id", "eq", "1"), comparison("name", "eq", "x")
                ),
                "((\"id\" = '1') AND (\"name\" = 'x'))",
                id="and-group",
            ),
            pytest.param(
                group(
                    "or",
                    comparison("id", "eq", "1"),
                    group(
                        "and",
                        comparison("id", "gt", "5"),
                        comparison("name", "eq", "x"),
                    ),
                ),
                "((\"id\" = '1') OR ((\"id\" > '5') AND (\"name\" = 'x')))",
                id="nested-group",
            ),
            pytest.param(
                group("or", comparison("id", "eq", "1"), negated=True),
                "NOT ((\"id\" = '1'))",
                id="negated-group",
            ),
        ],
    )
    async def test_renders_the_node(
        self, postgres: Postgres, node: dict[str, object], expected: str
    ) -> None:
        """Renders each operator, the quotes, the nesting and the negation."""
        assert await render_filter_node(postgres, node, COLUMNS) == expected

    @pytest.mark.parametrize(
        "node",
        [
            pytest.param(comparison("nope", "eq", "1"), id="unknown-column"),
            pytest.param(comparison("id", "fts", "x"), id="unknown-operator"),
            pytest.param(comparison("id", "eq", ["1", "2"]), id="list-for-eq"),
            pytest.param(comparison("id", "in", "1"), id="scalar-for-in"),
            pytest.param(comparison("id", "is", "maybe"), id="unknown-is-value"),
            pytest.param(group("and"), id="empty-group"),
            pytest.param(group("xor", comparison("id", "eq", "1")), id="unknown-group"),
            pytest.param({"kind": "leaf"}, id="unknown-node-kind"),
        ],
    )
    async def test_refuses_what_it_cannot_render(
        self, postgres: Postgres, node: dict[str, object]
    ) -> None:
        """Refuses a bad node with the status 400 of PostgREST."""
        with pytest.raises(psycopg.Error) as error:
            async with postgres.connection.transaction():
                await render_filter_node(postgres, node, COLUMNS)

        assert error.value.sqlstate == "PT400"


@pytest.mark.postgres
class TestRoutingUpgrade:
    """A database that has the older render_filter_node gets the new functions at the next sync run."""

    async def test_the_routing_templates_replace_the_function_with_the_old_parameter_names(
        self, postgres: Postgres
    ) -> None:
        """Replaces render_filter_node, and its callers read the new function."""
        mapping = {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
        await execute_sql(postgres, "postgres/create_old_filter_node", mapping=mapping)
        assert (
            await scalar(postgres, "SELECT data_proxy.render_filter_node('{}', '{}')")
            == "old"
        )

        for template in ROUTING_TEMPLATES:
            await postgres.connection.execute(
                render_template(f"postgres/views/routing/{template}", mapping)
            )

        arguments = await scalar(
            postgres,
            "SELECT pg_get_function_arguments('data_proxy.render_filter_node(jsonb, text[])'::regprocedure)",
        )
        assert arguments == "p_node jsonb, p_columns text[]"
        node = json.dumps(comparison("id", "eq", "1"))
        assert (
            await scalar(
                postgres,
                "SELECT data_proxy.render_filter_node(%s::jsonb, ARRAY['id'])",
                node,
            )
            == "(\"id\" = '1')"
        )
        assert (
            await scalar(
                postgres,
                "SELECT data_proxy.render_dl_where(%s::jsonb, ARRAY['id'])",
                json.dumps({"where": json.loads(node)}),
            )
            == "(\"id\" = '1')"
        )
        assert await scalar(
            postgres,
            "SELECT data_proxy.plan_aggregate_items(%s::jsonb, %s::jsonb)",
            json.dumps(aggregate({"fn": "count", "alias": "n"})["aggregate"]),
            json.dumps({}),
        ) == ["count(*) AS n"]
