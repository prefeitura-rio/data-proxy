"""Tests for the templates of the serving functions."""

import json

import psycopg
import pytest
from psycopg.sql import Identifier

from sync.settings import settings
from sync.templates import render_template
from tests.fixtures.types import Postgres
from tests.helpers import (
    act_as_token,
    fetch_all,
    publish_people_catalog,
    response_headers,
    scalar,
    set_request_headers,
)

CHECK_VIEWS = """
SELECT count(*) FROM duckdb.query(
    'SELECT 1 FROM duckdb_views() WHERE view_name = ''dl_snapshot_check'''
)
"""


@pytest.mark.postgres
class TestServingFunctions:
    """The functions that read the DuckLake catalog for the requests."""

    @pytest.mark.parametrize(
        ("pinned", "rows", "snapshot"),
        [
            pytest.param(None, 20, "2", id="latest-snapshot"),
            pytest.param("1", 10, "1", id="pinned-snapshot"),
        ],
    )
    async def test_view_function_snapshot_reads_the_requested_snapshot(
        self, ducklake_view: Postgres, pinned: str | None, rows: int, snapshot: str
    ) -> None:
        """Reads the requested snapshot."""
        if pinned is not None:
            await set_request_headers(ducklake_view, {"x-ducklake-snapshot": pinned})

        count = await scalar(
            ducklake_view,
            f'SELECT count(*) FROM "{ducklake_view.namespace.schema}".people',
        )

        assert count == rows
        assert await response_headers(ducklake_view) == {
            "X-Source": "ducklake",
            "X-DuckLake-Snapshot": snapshot,
        }

    async def test_view_can_be_read_twice_in_one_transaction_with_a_pushed_filter(
        self, ducklake_view: Postgres
    ) -> None:
        """PostgREST counts and then reads in one transaction. The second read must not open a savepoint, because DuckDB refuses it."""
        schema = ducklake_view.namespace.schema
        await set_request_headers(
            ducklake_view,
            {"x-ducklake-filter": json.dumps({"columns": ["cpf"], "limit": 3})},
        )
        total = await scalar(ducklake_view, f'SELECT count(*) FROM "{schema}".people')
        cursor = await ducklake_view.connection.execute(
            f'SELECT cpf FROM "{schema}".people'.encode()
        )
        rows = await cursor.fetchall()

        assert total == 3
        assert len(rows) == 3

    async def test_view_reads_only_the_pushed_columns_and_rows(
        self, ducklake_view: Postgres
    ) -> None:
        """The function returns the pushed number of rows, with a null in each column that the request skips."""
        schema = ducklake_view.namespace.schema
        await set_request_headers(
            ducklake_view,
            {"x-ducklake-filter": json.dumps({"columns": ["cpf"], "limit": 3})},
        )
        cursor = await ducklake_view.connection.execute(
            f'SELECT cpf, name FROM "{schema}".people'.encode()
        )
        pushed = await cursor.fetchall()
        await set_request_headers(ducklake_view, {})
        cursor = await ducklake_view.connection.execute(
            f'SELECT cpf, name FROM "{schema}".people ORDER BY cpf'.encode()
        )
        full = await cursor.fetchall()

        assert len(full) == 20
        assert len(pushed) == 3
        assert {name for _, name in pushed} == {None}
        assert {cpf for cpf, _ in pushed} <= {cpf for cpf, _ in full}
        assert None not in {name for _, name in full}

    async def test_view_pushes_a_numeric_order_with_the_limit(
        self, ducklake_view: Postgres
    ) -> None:
        """DuckDB keeps the top rows of a numeric order, so the pushed limit loses no row that the order wants."""
        schema = ducklake_view.namespace.schema
        await set_request_headers(
            ducklake_view,
            {
                "x-ducklake-filter": json.dumps(
                    {
                        "limit": 2,
                        "order": [{"column": "cpf", "descending": True}],
                    }
                )
            },
        )
        cursor = await ducklake_view.connection.execute(
            f'SELECT cpf FROM "{schema}".people ORDER BY cpf DESC'.encode()
        )
        pushed = await cursor.fetchall()

        assert pushed == [(29,), (28,)]

    async def test_view_applies_the_pushed_filter_before_the_limit(
        self, ducklake_view: Postgres
    ) -> None:
        """A limit counts the rows that pass the filter, as the policy predicate of a row rule does."""
        schema = ducklake_view.namespace.schema
        await set_request_headers(
            ducklake_view,
            {
                "x-ducklake-filter": json.dumps(
                    {
                        "where": {
                            "kind": "comparison",
                            "column": "cpf",
                            "operator": "gte",
                            "negated": False,
                            "value": "25",
                        },
                        "limit": 3,
                    }
                )
            },
        )
        cursor = await ducklake_view.connection.execute(
            f'SELECT cpf FROM "{schema}".people'.encode()
        )
        pushed = await cursor.fetchall()

        assert len(pushed) == 3
        assert min(cpf for (cpf,) in pushed) >= 25

    async def test_view_function_snapshot_rejects_a_pinned_snapshot_that_does_not_exist(
        self, ducklake_view: Postgres
    ) -> None:
        """Rejects a pinned snapshot that does not exist."""
        await set_request_headers(ducklake_view, {"x-ducklake-snapshot": "99"})

        with pytest.raises(psycopg.Error) as error:
            await ducklake_view.connection.execute(
                f'SELECT * FROM "{ducklake_view.namespace.schema}".people'.encode()
            )

        assert error.value.sqlstate == "PT404"

    @pytest.mark.parametrize(
        ("pinned", "checked"),
        [
            pytest.param(None, False, id="latest-snapshot-needs-no-check"),
            pytest.param("1", True, id="pinned-snapshot-is-checked"),
        ],
    )
    async def test_view_function_snapshot_checks_the_snapshot_only_when_the_request_pins_it(
        self, ducklake_view: Postgres, pinned: str | None, checked: bool
    ) -> None:
        """Checks the snapshot only when the request pins it."""
        if pinned is not None:
            await set_request_headers(ducklake_view, {"x-ducklake-snapshot": pinned})
        await scalar(
            ducklake_view,
            f'SELECT count(*) FROM "{ducklake_view.namespace.schema}".people',
        )

        views = await scalar(ducklake_view, CHECK_VIEWS)

        assert views == int(checked)

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            pytest.param(2, None, [(2, "insert", 10)], id="since-snapshot-to-latest"),
            pytest.param(
                1, 2, [(1, "insert", 10), (2, "insert", 10)], id="snapshot-range"
            ),
        ],
    )
    async def test_table_changes_function_returns_the_changed_rows(
        self,
        ducklake_view: Postgres,
        start: int,
        end: int | None,
        expected: list[tuple[int, str, int]],
    ) -> None:
        """Returns the changed rows, to a token whose role holds read_ducklake."""
        await act_as_token(ducklake_view, "dp_admin", ducklake_view.namespace.schema)
        rows = await fetch_all(
            ducklake_view,
            "postgres/select_changes",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"start_snapshot": start, "end_snapshot": end},
        )

        assert rows == expected

    async def test_table_changes_function_refuses_a_role_without_read_ducklake(
        self, ducklake_view: Postgres
    ) -> None:
        """The api role reads data but not the change feed of the catalog."""
        await act_as_token(ducklake_view, "dp_api", ducklake_view.namespace.schema)

        with pytest.raises(psycopg.Error) as error:
            await fetch_all(
                ducklake_view,
                "postgres/select_changes",
                mapping={"schema": ducklake_view.namespace.identifier},
                params={"start_snapshot": 1, "end_snapshot": 2},
            )

        assert error.value.sqlstate == "42501"

    async def test_table_files_function_lists_the_data_files_of_a_table(
        self, ducklake_view: Postgres
    ) -> None:
        """Each file has a path in the data location of the schema, and a size."""
        schema = ducklake_view.namespace.schema

        files = await fetch_all(
            ducklake_view,
            "postgres/select_table_files",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"table_name": "people"},
        )

        assert len(files) > 0
        assert all(
            str(path).endswith(".parquet") and "/" + schema + "/" in str(path)
            for path, _, _ in files
        )
        assert all(isinstance(size, int) and size > 0 for _, size, _ in files)

    async def test_table_files_function_lists_nothing_for_an_unknown_table(
        self, ducklake_view: Postgres
    ) -> None:
        """A table name that the catalog doesn't know gives no file."""
        files = await fetch_all(
            ducklake_view,
            "postgres/select_table_files",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"table_name": "nothing"},
        )

        assert files == []

    async def test_current_snapshot_function_snapshot_function_returns_the_latest_snapshot(
        self, ducklake_view: Postgres
    ) -> None:
        """Snapshot function returns the latest snapshot."""
        schema = ducklake_view.namespace.schema

        assert (
            await scalar(ducklake_view, f'SELECT "{schema}".dl_latest_snapshot()') == 2
        )

    async def test_serving_functions_of_two_schemas_attach_one_catalog_each(
        self, ducklake_view: Postgres
    ) -> None:
        """One backend serves several schemas, so each schema attaches its catalog under its own alias."""
        schema = ducklake_view.namespace.schema
        other = f"{schema}_other"
        publish_people_catalog(
            other, f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{other}"
        )
        await ducklake_view.connection.execute(
            render_template(
                "postgres/create_current_snapshot_function",
                {
                    "schema": ducklake_view.namespace.identifier,
                    "function": Identifier("dl_latest_snapshot_other"),
                    "metadata_schema": other,
                    "data_path": f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{other}",
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                },
            ).encode()
        )

        await scalar(ducklake_view, f'SELECT "{schema}".dl_latest_snapshot()')
        await scalar(ducklake_view, f'SELECT "{schema}".dl_latest_snapshot_other()')

        cursor = await ducklake_view.connection.execute(
            b"""
            SELECT r['database_name']::text
            FROM duckdb.query(
                'SELECT database_name FROM duckdb_databases() WHERE database_name LIKE ''dl_%'' ORDER BY 1'
            ) r
            """
        )
        assert [name for (name,) in await cursor.fetchall()] == sorted(
            [f"dl_{schema}", f"dl_{other}"]
        )

    async def test_serving_function_gives_the_backend_a_small_allocator_footprint(
        self, ducklake_view: Postgres
    ) -> None:
        """A backend that serves for an hour returns its freed memory, so a heavy answer does not stay in its heap."""
        schema = ducklake_view.namespace.schema

        await scalar(ducklake_view, f'SELECT "{schema}".dl_latest_snapshot()')

        cursor = await ducklake_view.connection.execute(
            b"""
            SELECT r['name']::text, r['value']::text
            FROM duckdb.query(
                'SELECT name, value FROM duckdb_settings() WHERE name IN (''allocator_background_threads'', ''allocator_flush_threshold'') ORDER BY 1'
            ) r
            """
        )
        assert await cursor.fetchall() == [
            ("allocator_background_threads", "true"),
            ("allocator_flush_threshold", "16.0 MiB"),
        ]
