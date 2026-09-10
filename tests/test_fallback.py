"""Tests for the BigQuery fallback view generation and cache invalidation."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg import Connection
from redis.asyncio import Redis

from dp.authorization import unit_predicate
from dp.fallback import (
    bigquery_column_expr,
    bq_function_sql,
    bq_view_sql,
    column_types_from_duckdb,
    create_bq_views,
    flush_cache,
    is_struct_or_array,
    postgres_column_cast,
    return_type_for,
    rls_where_clause,
)
from dp.models import FullTable, SchemaConfig, SyncConfig, UnitMapping
from dp.settings import Settings
from tests.conftest import PostgresTestNamespace
from tests.helpers import execute_sql, sync_config


@dataclass(frozen=True, slots=True)
class FallbackColumn:
    """One BigQuery column and its expected fallback SQL representations."""

    name: str
    duckdb_type: str
    is_nested: bool
    postgres_cast: str
    return_type: str


FALLBACK_COLUMNS = [
    FallbackColumn("data", "STRUCT(x VARCHAR)", True, "::text", '"data" text'),
    FallbackColumn("items", "ARRAY(VARCHAR)", True, "::text", '"items" text'),
    FallbackColumn("values", "LIST(VARCHAR)", True, "::text", '"values" text'),
    FallbackColumn("name", "VARCHAR", False, "::text", '"name" text'),
    FallbackColumn("born", "DATE", False, "::date", '"born" date'),
    FallbackColumn("active", "BOOLEAN", False, "::boolean", '"active" boolean'),
    FallbackColumn("count", "INTEGER", False, "::bigint", '"count" bigint'),
    FallbackColumn("total", "BIGINT", False, "::bigint", '"total" bigint'),
]


class TestIsStructOrArray:
    """Type detection for DuckDB STRUCT and ARRAY types."""

    @pytest.mark.parametrize("column", FALLBACK_COLUMNS)
    def test_type_detection(self, column: FallbackColumn) -> None:
        """Detects STRUCT, ARRAY, and LIST types correctly."""
        assert is_struct_or_array(column.duckdb_type) is column.is_nested

    def test_lowercase_struct_is_detected(self) -> None:
        """Lowercase struct type is detected."""
        assert is_struct_or_array("struct(x int)")


class TestBigqueryColumnExpr:
    """DuckDB SELECT expression generation for BigQuery columns."""

    def test_struct_wrapped_with_to_json(self) -> None:
        """STRUCT column is wrapped with to_json()."""
        assert (
            bigquery_column_expr("data", "STRUCT(x VARCHAR)")
            == 'to_json("data") AS "data"'
        )

    def test_varchar_passes_through(self) -> None:
        """VARCHAR column passes through unchanged."""
        assert bigquery_column_expr("name", "VARCHAR") == '"name"'


class TestPostgresColumnCast:
    """PostgreSQL cast expression generation for duckdb.query() columns."""

    @pytest.mark.parametrize("column", FALLBACK_COLUMNS)
    def test_cast(self, column: FallbackColumn) -> None:
        """Each type maps to the correct PostgreSQL cast."""
        result = postgres_column_cast(column.name, column.duckdb_type)
        assert column.postgres_cast in result


class TestReturnTypeFor:
    """PostgreSQL type declaration for RETURNS TABLE clause."""

    @pytest.mark.parametrize("column", FALLBACK_COLUMNS)
    def test_return_type(self, column: FallbackColumn) -> None:
        """Each type maps to the correct RETURNS TABLE declaration."""
        assert return_type_for(column.name, column.duckdb_type) == column.return_type


class TestUnitPredicate:
    """OR-joined unit type and column predicate for RLS."""

    def test_single_mapping(self) -> None:
        """One mapping produces one predicate clause."""
        mappings = [UnitMapping(column="id_cras", unit_type="cras")]
        result = unit_predicate(mappings).as_string(None)
        assert "p.unit_type = 'cras'" in result
        assert 'p.unit_id = "id_cras"::text' in result

    def test_multiple_mappings_joined_with_or(self) -> None:
        """Multiple mappings are joined with OR."""
        mappings = [
            UnitMapping(column="id_cras", unit_type="cras"),
            UnitMapping(column="id_escola", unit_type="escola"),
        ]
        result = unit_predicate(mappings).as_string(None)
        assert " OR " in result
        assert "cras" in result
        assert "escola" in result


class TestRlsWhereClause:
    """BigQuery WHERE clause generation from RLS unit mappings."""

    def test_no_rls_returns_empty(self) -> None:
        """Table without RLS returns an empty string."""
        table = FullTable(name="p.app.t", resolved_schema="app")
        assert rls_where_clause("app", table) == ""

    def test_no_claim_returns_empty(self) -> None:
        """Schema with no claim returns an empty string."""
        table = FullTable(
            name="p.app.t",
            resolved_schema="app",
            rls=[UnitMapping(column="id_unit", unit_type="unit")],
        )
        config = SyncConfig.model_construct(
            schemas={"app": SchemaConfig.model_construct(tables=[table], claim=None)}
        )
        with patch.object(
            Settings, "sync_config", new_callable=lambda: property(lambda _: config)
        ):
            assert rls_where_clause("app", table) == ""

    def test_with_rls_and_claim_renders_template(
        self, test_settings: Settings, sync_config_path: Path
    ) -> None:
        """Table with RLS and a claim renders the WHERE clause template."""
        table = FullTable(
            name="p.app.t",
            resolved_schema="app",
            rls=[UnitMapping(column="id_unit", unit_type="unit")],
        )
        config = sync_config([table], claim="preferred_username")
        sync_config_path.write_text(config.model_dump_json())
        result = rls_where_clause("app", table)
        assert "access_policy" in result
        assert "app.claim_preferred_username" in result


class TestFallbackMockedColumnTypes:
    """Column type retrieval through a mocked PostgreSQL query."""

    def test_returns_column_type_pairs(
        self,
    ) -> None:
        """Column types are returned as (name, type) tuples."""
        table = FullTable(name="p.app.t", resolved_schema="app")
        with patch("dp.fallback.execute_sql") as mock_execute:
            mock_execute.return_value.fetchall.return_value = [
                ("id", "VARCHAR"),
                ("data", "STRUCT(x VARCHAR)"),
            ]
            result = column_types_from_duckdb(MagicMock(spec=Connection), table)
        assert result == [("id", "VARCHAR"), ("data", "STRUCT(x VARCHAR)")]


class TestFallbackPostgresIntegration:
    """Fallback behavior against the PostgreSQL and pg_duckdb fixture."""

    def test_pg_duckdb_query_fixture_returns_rows(
        self, postgres: Connection[tuple[object, ...]]
    ) -> None:
        """The Testcontainers fixture can execute a DuckDB query."""
        assert postgres.execute(
            "SELECT * FROM duckdb.query('SELECT 1 AS id')"
        ).fetchall() == [(1,)]

    def test_generated_function_returns_mocked_duckdb_rows(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
        duckdb_raw_query_stub: None,
        test_settings: Settings,
        sync_config_path: Path,
    ) -> None:
        """A generated no-RLS function executes against a controlled DuckDB view."""
        table = FullTable(
            name=f"p.{namespace.schema}.t", resolved_schema=namespace.schema
        )
        config = sync_config(
            [table], schema_name=namespace.schema, claim="preferred_username"
        )
        sync_config_path.write_text(config.model_dump_json())

        execute_sql(
            postgres,
            "postgres/create_fallback_access_policy",
            mapping={"schema": namespace.schema},
        )
        postgres.commit()
        execute_sql(
            postgres,
            "postgres/create_fallback_duckdb_view",
            mapping={"schema": namespace.schema},
        )
        postgres.commit()
        execute_sql(postgres, "postgres/create_fallback_raw_query_stub")

        columns = [
            ("id", "INTEGER"),
            ("born", "DATE"),
            ("active", "BOOLEAN"),
            ("data", "STRUCT(x INTEGER)"),
            ("select", "INTEGER"),
        ]

        function_sql = bq_function_sql(namespace.schema, table, columns)

        assert "jsonb" not in function_sql

        postgres.execute(function_sql.encode())
        postgres.commit()
        postgres.execute(
            "SET LOCAL duckdb.unsafe_allow_execution_inside_functions = 'on'"
        )
        postgres.execute(f"SET LOCAL app.claim_schemas = '{namespace.schema}'".encode())
        view_sql = bq_view_sql(namespace.schema, table, columns)
        assert '"data"::jsonb AS "data"' in view_sql

        postgres.execute(view_sql.encode())
        assert execute_sql(
            postgres,
            "postgres/select_fallback_view",
            mapping={"schema": namespace.schema},
        ).fetchall() == [(7, date(2024, 1, 2), True, {"x": 1}, 9)]

    def test_generated_function_keeps_overlapping_unit_ids_on_their_mapping_column(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
        duckdb_raw_query_stub: None,
        test_settings: Settings,
        sync_config_path: Path,
    ) -> None:
        """The real function sends a cras grant only to the cras filter column."""
        table = FullTable(
            name=f"p.{namespace.schema}.t",
            resolved_schema=namespace.schema,
            rls=[
                UnitMapping(column="id_cras", unit_type="cras"),
                UnitMapping(column="id_escola", unit_type="escola"),
            ],
        )
        sync_config_path.write_text(
            sync_config(
                [table], schema_name=namespace.schema, claim="preferred_username"
            ).model_dump_json()
        )
        execute_sql(
            postgres,
            "postgres/create_fallback_access_policy",
            mapping={"schema": namespace.schema},
        )
        execute_sql(
            postgres,
            "postgres/insert_fallback_cras_grant",
            mapping={"schema": namespace.schema},
        )
        postgres.commit()
        execute_sql(
            postgres,
            "postgres/create_fallback_duckdb_view",
            mapping={"schema": namespace.schema},
        )
        postgres.commit()
        execute_sql(postgres, "postgres/create_fallback_multi_unit_raw_query_stub")
        postgres.execute(
            bq_function_sql(namespace.schema, table, [("id", "INTEGER")]).encode()
        )
        postgres.commit()
        postgres.execute(
            "SET LOCAL duckdb.unsafe_allow_execution_inside_functions = 'on'"
        )
        postgres.execute(f"SET LOCAL app.claim_schemas = '{namespace.schema}'".encode())
        postgres.execute("SET LOCAL app.claim_preferred_username = 'alice'")
        assert execute_sql(
            postgres,
            "postgres/select_fallback_function",
            mapping={"schema": namespace.schema},
        ).fetchall() == [(7,)]

    def test_generated_function_executes_and_denies_out_of_scope_user(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
        test_settings: Settings,
        sync_config_path: Path,
    ) -> None:
        """A real PostgreSQL function returns no rows before DuckDB when out of scope."""
        table = FullTable(name="p.app.t", resolved_schema="app")
        config = sync_config(
            [table], schema_name=namespace.schema, claim="preferred_username"
        )
        sync_config_path.write_text(config.model_dump_json())
        postgres.execute(
            bq_function_sql(namespace.schema, table, [("id", "INTEGER")]).encode()
        )
        postgres.execute("SET LOCAL app.claim_schemas = 'other'")
        assert (
            execute_sql(
                postgres,
                "postgres/select_fallback_function",
                mapping={"schema": namespace.schema},
            ).fetchall()
            == []
        )


class TestFallbackSqlRendering:
    """Pure fallback SQL rendering behavior."""

    def test_generates_function_with_rls(
        self, test_settings: Settings, sync_config_path: Path
    ) -> None:
        """Function SQL contains the table name, columns, and RLS logic."""
        table = FullTable(
            name="p.app.t",
            resolved_schema="app",
            rls=[UnitMapping(column="id_unit", unit_type="unit")],
        )
        config = sync_config([table], claim="preferred_username")
        sync_config_path.write_text(config.model_dump_json())
        result = bq_function_sql("app", table, [("id", "VARCHAR"), ("name", "VARCHAR")])
        assert "CREATE OR REPLACE FUNCTION" in result
        assert "bigquery_scan" in result
        assert "SECURITY DEFINER" in result
        assert "duckdb.raw_query" in result
        assert "duckdb.query" in result

    def test_multi_unit_sql_keeps_each_unit_type_on_its_mapping_column(
        self, test_settings: Settings, sync_config_path: Path
    ) -> None:
        """Overlapping identifiers remain scoped to their configured column."""
        table = FullTable(
            name="p.app.t",
            resolved_schema="app",
            rls=[
                UnitMapping(column="id_cras", unit_type="cras"),
                UnitMapping(column="id_escola", unit_type="escola"),
            ],
        )
        sync_config_path.write_text(
            sync_config([table], claim="preferred_username").model_dump_json()
        )
        result = bq_function_sql("app", table, [("id", "INTEGER")])
        assert 'JOIN "app".access_policy p ON p.unit_type = t.ut' in result
        assert "GROUP BY t.col" in result

    def test_generates_function_without_rls(
        self, test_settings: Settings, sync_config_path: Path
    ) -> None:
        """Function SQL for a table without RLS still generates correctly."""
        table = FullTable(name="p.app.t", resolved_schema="app")
        sync_config_path.write_text(sync_config([table]).model_dump_json())
        assert "CREATE OR REPLACE FUNCTION" in bq_function_sql(
            "app", table, [("id", "VARCHAR")]
        )


class TestFallbackSqlViewRendering:
    """CREATE OR REPLACE VIEW statement rendering."""

    def test_generates_passthrough_view(self) -> None:
        """View SQL is a simple passthrough to the function."""
        table = FullTable(name="p.app.t", resolved_schema="app")
        result = bq_view_sql("app", table, [("id", "INTEGER")])
        assert "CREATE OR REPLACE VIEW" in result
        assert "t_bq" in result
        assert "t_bq_fn" in result


class TestFallbackMockedServices:
    """Fallback orchestration against mocked PostgreSQL and Redis services."""

    def test_creates_views_for_fallback_tables(
        self, test_settings: Settings, sync_config_path: Path
    ) -> None:
        """Views are created for tables with fallback enabled."""
        table = FullTable(name="p.app.t", resolved_schema="app", fallback=True)
        config = sync_config([table])
        sync_config_path.write_text(config.model_dump_json())
        conn = MagicMock(spec=Connection)

        with (
            patch(
                "dp.fallback.column_types_from_duckdb", return_value=[("id", "VARCHAR")]
            ),
            patch("dp.fallback.bq_function_sql", return_value="FN"),
            patch("dp.fallback.bq_view_sql", return_value="VIEW"),
        ):
            create_bq_views(conn, config)

        conn.execute.assert_any_call(b"FN")
        conn.execute.assert_any_call(b"VIEW")
        conn.commit.assert_called_once()

    def test_skips_tables_with_fallback_disabled(self) -> None:
        """Tables with fallback=False are skipped."""
        table = FullTable(name="p.app.t", resolved_schema="app", fallback=False)
        config = sync_config([table])
        conn = MagicMock(spec=Connection)

        with patch("dp.fallback.column_types_from_duckdb") as mock_columns:
            create_bq_views(conn, config)

        mock_columns.assert_not_called()
        conn.execute.assert_not_called()


class TestFallbackMockedCache:
    """Cache invalidation against fake Redis."""

    @pytest.mark.asyncio
    async def test_flush_cache_calls_flushdb(
        self,
        redis: Redis,
        redis_factory: Callable[[Redis], Callable[[Settings, int | None], Redis]],
    ) -> None:
        """flush_cache calls flushdb on the Redis client."""
        with patch.object(Settings, "redis", redis_factory(redis)):
            await flush_cache(db=1)

    @pytest.mark.asyncio
    async def test_flush_cache_uses_correct_db(
        self,
        redis_factory: Callable[[Redis], Callable[[Settings, int | None], Redis]],
    ) -> None:
        """flush_cache connects to the specified database number."""
        mock_redis = MagicMock()
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=None)
        mock_redis.flushdb = AsyncMock()

        with patch.object(Settings, "redis", redis_factory(mock_redis)):
            await flush_cache(db=1)

        mock_redis.flushdb.assert_awaited()
