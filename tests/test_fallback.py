"""Tests for the BigQuery fallback view generation and cache invalidation."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg import AsyncConnection
from psycopg.sql import Composable
from redis.asyncio import Redis

from dp.fallback import (
    RLS,
    bigquery_column_expr,
    column_types_from_duckdb,
    create_bq_views,
    is_nested_or_json,
    postgres_column_cast,
    return_type_for,
    rls_where_clause,
)
from dp.models import FullTable, SchemaConfig, SyncConfig, UnitMapping
from dp.settings import Settings
from dp.state import clear_response_cache
from tests.helpers import sync_config


def configure_redis(redis_client: Redis) -> Callable[[Settings, int | None], Redis]:
    """Return a Settings.redis replacement for one test client."""
    return lambda _settings, db=None: redis_client


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
    FallbackColumn("payload", "JSON", True, "::text", '"payload" text'),
    FallbackColumn("name", "VARCHAR", False, "::text", '"name" text'),
    FallbackColumn("born", "DATE", False, "::date", '"born" date'),
    FallbackColumn("active", "BOOLEAN", False, "::boolean", '"active" boolean'),
    FallbackColumn("count", "INTEGER", False, "::bigint", '"count" bigint'),
    FallbackColumn("total", "BIGINT", False, "::bigint", '"total" bigint'),
]


class TestIsNestedOrJson:
    """Type detection for the DuckDB types that carry JSON."""

    @pytest.mark.parametrize("column", FALLBACK_COLUMNS)
    @pytest.mark.asyncio
    async def test_type_detection(self, column: FallbackColumn) -> None:
        """Detects STRUCT, ARRAY, LIST, and JSON types correctly."""
        assert is_nested_or_json(column.duckdb_type) is column.is_nested

    @pytest.mark.asyncio
    async def test_lowercase_struct_is_detected(self) -> None:
        """Lowercase struct type is detected."""
        assert is_nested_or_json("struct(x int)")

    @pytest.mark.asyncio
    async def test_lowercase_json_is_detected(self) -> None:
        """Lowercase json type is detected."""
        assert is_nested_or_json("json")


class TestBigqueryColumnExpr:
    """DuckDB SELECT expression generation for BigQuery columns."""

    @pytest.mark.asyncio
    async def test_struct_wrapped_with_to_json(self) -> None:
        """STRUCT column is wrapped with to_json()."""
        assert (
            bigquery_column_expr("data", "STRUCT(x VARCHAR)")
            == 'to_json("data") AS "data"'
        )

    @pytest.mark.asyncio
    async def test_varchar_passes_through(self) -> None:
        """VARCHAR column passes through unchanged."""
        assert bigquery_column_expr("name", "VARCHAR") == '"name"'


class TestPostgresColumnCast:
    """PostgreSQL cast expression generation for duckdb.query() columns."""

    @pytest.mark.parametrize("column", FALLBACK_COLUMNS)
    @pytest.mark.asyncio
    async def test_cast(self, column: FallbackColumn) -> None:
        """Each type maps to the correct PostgreSQL cast."""
        result = postgres_column_cast(column.name, column.duckdb_type)
        assert column.postgres_cast in result


class TestReturnTypeFor:
    """PostgreSQL type declaration for RETURNS TABLE clause."""

    @pytest.mark.parametrize("column", FALLBACK_COLUMNS)
    @pytest.mark.asyncio
    async def test_return_type(self, column: FallbackColumn) -> None:
        """Each type maps to the correct RETURNS TABLE declaration."""
        assert return_type_for(column.name, column.duckdb_type) == column.return_type


class TestRlsWhereClause:
    """BigQuery WHERE clause generation from RLS unit mappings."""

    def test_empty_mappings_disable_rls(self) -> None:
        """No unit mappings produce a disabled RLS fragment."""
        assert RLS.from_mappings(None).enabled == "false"

    def test_mappings_enable_rls(self) -> None:
        """Unit mappings produce an enabled RLS fragment."""
        rls = RLS.from_mappings([UnitMapping(column="id_unit", unit_type="unit")])
        assert rls.enabled == "true"

    @pytest.mark.asyncio
    async def test_no_rls_returns_empty(self) -> None:
        """Table without RLS returns an empty string."""
        table = FullTable(name="p.app.t", resolved_schema="app")
        assert rls_where_clause("app", table) == ""

    @pytest.mark.asyncio
    async def test_no_claim_returns_empty(self) -> None:
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

    @pytest.mark.asyncio
    async def test_rls_with_claim_renders_the_access_policy(self) -> None:
        """A table with RLS and a schema claim renders the access-policy condition."""
        table = FullTable(
            name="p.app.t",
            resolved_schema="app",
            rls=[UnitMapping(column="id_unit", unit_type="unit")],
        )
        config = SyncConfig.model_construct(
            schemas={
                "app": SchemaConfig.model_construct(
                    tables=[table], claim="preferred_username"
                )
            }
        )
        with patch.object(
            Settings, "sync_config", new_callable=lambda: property(lambda _: config)
        ):
            rendered = cast(Composable, rls_where_clause("app", table)).as_string(None)

        assert "access_policy" in rendered
        assert "app.claim_preferred_username" in rendered

    @pytest.mark.usefixtures("test_settings")
    @pytest.mark.asyncio
    async def test_returns_column_type_pairs(
        self,
    ) -> None:
        """Column types are returned as (name, type) tuples."""
        table = FullTable(name="p.app.t", resolved_schema="app")
        with patch("dp.fallback.execute_sql") as fake_execute:
            fake_execute.return_value.fetchall.return_value = [
                ("id", "VARCHAR"),
                ("data", "STRUCT(x VARCHAR)"),
            ]
            result = await column_types_from_duckdb(
                AsyncMock(spec=AsyncConnection), table
            )
        assert result == [("id", "VARCHAR"), ("data", "STRUCT(x VARCHAR)")]


class TestFallbackMockedServices:
    """Fallback orchestration against mocked PostgreSQL and Redis services."""

    @pytest.mark.usefixtures("test_settings")
    @pytest.mark.asyncio
    async def test_creates_views_for_fallback_tables(
        self, sync_config_path: Path
    ) -> None:
        """Views are created for tables with fallback enabled."""
        table = FullTable(name="p.app.t", resolved_schema="app", fallback=True)
        config = sync_config([table])
        sync_config_path.write_text(config.model_dump_json())
        conn = AsyncMock(spec=AsyncConnection)

        with (
            patch(
                "dp.fallback.column_types_from_duckdb", return_value=[("id", "VARCHAR")]
            ),
            patch("dp.fallback.execute_sql") as execute,
        ):
            await create_bq_views(conn, config)

        assert execute.await_count == 3
        conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_tables_with_fallback_disabled(self) -> None:
        """Tables with fallback=False are skipped."""
        table = FullTable(name="p.app.t", resolved_schema="app", fallback=False)
        config = sync_config([table])
        conn = AsyncMock(spec=AsyncConnection)

        with patch("dp.fallback.column_types_from_duckdb") as fake_columns:
            await create_bq_views(conn, config)

        fake_columns.assert_not_called()
        conn.execute.assert_not_called()


class TestFallbackMockedCache:
    """Cache invalidation against fake Redis."""

    @pytest.mark.asyncio
    async def test_clear_response_cache_calls_flushdb(
        self,
        redis: Redis,
    ) -> None:
        """clear_response_cache calls flushdb on the Redis client."""
        with patch.object(Settings, "redis", configure_redis(redis)):
            await clear_response_cache(db=1)

    @pytest.mark.asyncio
    async def test_clear_response_cache_uses_correct_db(
        self,
        redis: Redis,
    ) -> None:
        """clear_response_cache connects to the specified database number."""
        fake_redis = MagicMock()
        fake_redis.__aenter__ = AsyncMock(return_value=fake_redis)
        fake_redis.__aexit__ = AsyncMock(return_value=None)
        fake_redis.flushdb = AsyncMock()

        with patch.object(Settings, "redis", configure_redis(fake_redis)):
            await clear_response_cache(db=1)

        fake_redis.flushdb.assert_awaited()
