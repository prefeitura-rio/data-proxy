{#
{
  "kind": "template",
  "description": "Render the private helper that reads one DuckLake table at a snapshot.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "app_schema": "PostgreSQL schema that holds the routing functions.",
    "function": "PostgreSQL function being created.",
    "columns": "Structured SQL-safe column metadata.",
    "duckdb_view": "DuckDB intermediate view name.",
    "source": "FROM clause body, e.g. dl.table.",
    "catalog_local_path": "Local filesystem path to the SQLite catalog file.",
    "data_path": "S3 data path for the DuckLake attachment."
  }
}
#}
-- noqa: disable=PRS,LT05
{% from "postgres/macros.sql" import column_projection, return_query_select, duckdb_query_select, ducklake_attach %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}(p_where text, p_arg text)
RETURNS TABLE(
{% for column in columns %}
{{ column.name }} {{ column.return_type }}{% if not loop.last %}, {% endif %}
{% endfor %}
) AS $$
DECLARE
  v_snapshot bigint;
BEGIN
{{ ducklake_attach(catalog_local_path, data_path) }}

  v_snapshot := p_arg::bigint;

  PERFORM {{ app_schema }}.assert_snapshot_exists(v_snapshot);

  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW {{ duckdb_view }} AS ' ||
    'SELECT {{ column_projection(columns) }} ' ||
    'FROM {{ source }} AT (VERSION => ' || v_snapshot || ') ' || p_where
  );

  RETURN QUERY
{{ return_query_select(columns) }}
{{ duckdb_query_select(columns, duckdb_view) }};
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}(text, text) FROM PUBLIC
