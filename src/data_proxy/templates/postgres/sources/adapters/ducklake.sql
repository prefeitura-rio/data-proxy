{#
{
  "kind": "template",
  "description": "Render the private helper that prepares a DuckLake view at a snapshot.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "app_schema": "PostgreSQL schema that holds the routing functions.",
    "function": "PostgreSQL function being created.",
    "duckdb_view": "DuckDB intermediate view name.",
    "source": "FROM clause body, e.g. dl.table.",
    "catalog_local_path": "Local filesystem path to the SQLite catalog file.",
    "data_path": "S3 data path for the DuckLake attachment."
  }
}
#}
-- noqa: disable=PRS,LT05
{% from "postgres/macros.sql" import column_projection, ducklake_attach %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}(p_where text, p_arg text)
RETURNS void AS $$
DECLARE
  v_snapshot bigint;
BEGIN
{{ ducklake_attach(catalog_local_path, data_path) }}

  v_snapshot := p_arg::bigint;

  IF {{ app_schema }}.requested_snapshot() IS NOT NULL THEN
    PERFORM {{ app_schema }}.assert_snapshot_exists(v_snapshot);
  END IF;

  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW {{ duckdb_view }} AS ' ||
    'SELECT {{ column_projection(columns) }} ' ||
    'FROM {{ source }} AT (VERSION => ' || v_snapshot || ') ' || p_where
  );
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}(text, text) FROM PUBLIC
