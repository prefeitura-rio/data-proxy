"""Rendering tests for SQL templates with Jinja logic."""

from collections.abc import Mapping

from psycopg.sql import Identifier, Literal

from data_proxy.templates import render_template
from data_proxy.types import TemplateValue


def render_template_text(path: str, mapping: Mapping[str, TemplateValue]) -> str:
    """Render one application template and return the SQL text."""
    return render_template(path, mapping)


class TestAccessPolicyCheck:
    """Access policy check template behavior tests."""

    def test_enables_rls_and_creates_scoped_policy(self) -> None:
        """Enable RLS and create an access policy with IN (SELECT) checks."""
        sql = render_template_text(
            "postgres/access_policy_check",
            {
                "schema": Identifier("pic"),
                "table": Identifier("people"),
                "scope": Literal("true"),
                "claim_setting": Literal("'app.claim_sub'"),
                "rls_mappings": [
                    {"column": "unit_id", "unit_type": "unit"},
                ],
            },
        )
        assert "ENABLE ROW LEVEL SECURITY" in sql
        assert "DROP POLICY IF EXISTS access_policy_scoped" in sql
        assert "CREATE POLICY access_policy_scoped" in sql
        assert "IN (SELECT unit_id" in sql
        assert "is_admin" in sql


class TestInitAccessPolicy:
    """Init access policy template behavior tests."""

    def test_creates_tables_trigger_and_policy(self) -> None:
        """Create access_policy, access_log, trigger, and read policy."""
        sql = render_template_text(
            "postgres/init_access_policy",
            {
                "schema": Identifier("pic"),
                "user_role": Identifier("dp_user"),
                "scope": Literal("true"),
            },
        )
        assert "CREATE TABLE IF NOT EXISTS" in sql
        assert "access_policy" in sql
        assert "access_log" in sql
        assert "log_access_policy_change" in sql
        assert "access_log_trigger" in sql
        assert "user_read" in sql
        assert "ENABLE ROW LEVEL SECURITY" in sql


class TestCreateBqFunction:
    """Create BigQuery fallback function template behavior tests."""

    def test_creates_security_definer_function_with_bigquery_scan(self) -> None:
        """Create a SECURITY DEFINER function that scans BigQuery via DuckDB."""
        sql = render_template_text(
            "postgres/create_view_function",
            {
                "schema": Identifier("pic"),
                "function": Identifier("people_bq_fn"),
                "columns": [
                    {
                        "name": "id",
                        "key": "'id'",
                        "is_json": False,
                        "pg_type": "bigint",
                        "return_type": "bigint",
                    },
                    {
                        "name": "payload",
                        "key": "'payload'",
                        "is_json": True,
                        "pg_type": "jsonb",
                        "return_type": "text",
                    },
                ],
                "claim_setting": "'app.claim_sub'",
                "scope": Literal("true"),
                "has_rls": "true",
                "rls_mappings": [
                    {"column": "unit_id", "unit_type": "unit"},
                ],
                "duckdb_view": "bq_fallback_pic_people",
                "source": "bigquery_scan(''project.dataset.people'')",
                "source_prefix": "LOAD bigquery; ",
            },
        )
        assert "CREATE OR REPLACE FUNCTION" in sql
        assert "RETURNS TABLE" in sql
        assert "SECURITY DEFINER" in sql
        assert "bigquery_scan" in sql
        assert "duckdb.query" in sql
        assert "to_json(payload) AS payload" in sql
        assert "LOAD bigquery" in sql


class TestCreateDucklakeFunction:
    """Create DuckLake-backed query function template behavior tests."""

    def test_creates_security_definer_function_with_catalog_refresh(self) -> None:
        """Create a SECURITY DEFINER function that attaches DuckLake on revision change."""
        sql = render_template_text(
            "postgres/create_view_function",
            {
                "schema": Identifier("pic"),
                "function": Identifier("people_fn"),
                "columns": [
                    {
                        "name": "id",
                        "key": "'id'",
                        "is_json": False,
                        "pg_type": "bigint",
                        "return_type": "bigint",
                    },
                ],
                "claim_setting": "'app.claim_sub'",
                "scope": Literal("true"),
                "has_rls": "false",
                "rls_mappings": [],
                "duckdb_view": "ducklake_pic_people",
                "source": "dl.people",
                "catalog_local_path": "/var/lib/ducklake/catalogs/pic/catalog.sqlite",
                "data_path": "s3://bucket/ducklake/pic",
            },
        )
        assert "CREATE OR REPLACE FUNCTION" in sql
        assert "SECURITY DEFINER" in sql
        assert "ducklake:sqlite" in sql
        assert "ATTACH" in sql
        assert "duckdb.recycle_ddb" in sql
        assert "dl.people" in sql
        assert "bigquery_scan" not in sql


class TestCreateBqView:
    """Create BigQuery fallback view template behavior tests."""

    def test_creates_view_with_column_projection(self) -> None:
        """Create a view selecting columns from the fallback function."""
        sql = render_template_text(
            "postgres/create_view",
            {
                "schema": Identifier("pic"),
                "view": Identifier("people_bq"),
                "function": Identifier("people_bq_fn"),
                "columns": ["id", "payload::jsonb AS payload"],
            },
        )
        assert "CREATE OR REPLACE VIEW" in sql
        assert "id," in sql
        assert "payload::jsonb AS payload" in sql
        assert "people_bq_fn" in sql


class TestDuckdbWriteAll:
    """DuckDB write_all template behavior tests."""

    def test_copies_with_replace_for_json_columns(self) -> None:
        """Copy with REPLACE projection for JSON columns."""
        sql = render_template_text(
            "duckdb/write_all",
            {
                "json_columns": ['"payload"'],
                "bq_table": Literal("'project.dataset.people'"),
                "path": Literal("'s3://bucket/data.parquet'"),
            },
        )
        assert "COPY" in sql
        assert "SELECT * REPLACE" in sql
        assert 'to_json("payload") AS "payload"' in sql
        assert "bigquery_scan" in sql
        assert "FORMAT PARQUET" in sql

    def test_copies_plain_select_without_json_columns(self) -> None:
        """Copy with a plain SELECT * when no JSON columns are given."""
        sql = render_template_text(
            "duckdb/write_all",
            {
                "json_columns": [],
                "bq_table": Literal("'project.dataset.people'"),
                "path": Literal("'s3://bucket/data.parquet'"),
            },
        )
        assert "COPY" in sql
        assert "SELECT *" in sql
        assert "REPLACE" not in sql


class TestDuckdbWritePartition:
    """DuckDB write_partition template behavior tests."""

    def test_copies_with_range_predicate(self) -> None:
        """Copy with a WHERE >= AND < range predicate."""
        sql = render_template_text(
            "duckdb/write_partition",
            {
                "json_columns": [],
                "bq_table": Literal("'project.dataset.people'"),
                "column": Identifier("id"),
                "lower": "10",
                "upper": "20",
                "path": Literal("'s3://bucket/data.parquet'"),
            },
        )
        assert "COPY" in sql
        assert "WHERE" in sql
        assert ">= 10" in sql
        assert "< 20" in sql


class TestDuckdbWriteRemainder:
    """DuckDB write_remainder template behavior tests."""

    def test_copies_with_null_and_range_checks(self) -> None:
        """Copy with IS NULL OR < OR >= checks for the remainder partition."""
        sql = render_template_text(
            "duckdb/write_remainder",
            {
                "json_columns": [],
                "bq_table": Literal("'project.dataset.people'"),
                "column": Identifier("id"),
                "lower": "0",
                "upper": "100",
                "path": Literal("'s3://bucket/data.parquet'"),
            },
        )
        assert "COPY" in sql
        assert "IS NULL" in sql
        assert "< 0" in sql
        assert ">= 100" in sql
