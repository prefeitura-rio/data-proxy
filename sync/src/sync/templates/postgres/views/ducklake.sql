{#
{
  "kind": "template",
  "description": "Render the private helper that prepares a DuckLake view at a snapshot.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "app_schema": "PostgreSQL schema that holds the routing functions.",
    "function": "PostgreSQL function being created.",
    "duckdb_view": "DuckDB intermediate view name.",
    "source": "FROM clause body, e.g. \"dl_<schema>\".table.",
    "metadata_schema": "Schema of the catalog database that holds the DuckLake tables.",
    "data_path": "S3 data path for the DuckLake attachment."
  }
}
#}
-- noqa: disable=CP02,PRS,LT05
{% from "postgres/macros.sql" import column_projection, ducklake_attach %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}(p_where text, p_arg text, p_validate_snapshot boolean)
RETURNS void AS $$
DECLARE
  v_snapshot bigint;
BEGIN
{{ ducklake_attach(metadata_schema, data_path) }}

  v_snapshot := p_arg::bigint;

  IF p_validate_snapshot THEN
    PERFORM {{ app_schema }}.assert_snapshot_exists(v_snapshot, '{{ metadata_schema }}');
  END IF;

  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW {{ duckdb_view }} AS ' ||
    'SELECT {{ column_projection(columns) }} ' ||
    'FROM {{ source }} AT (VERSION => ' || v_snapshot || ') ' ||
    coalesce(p_where, '')
  );
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}(text, text, boolean) FROM PUBLIC
