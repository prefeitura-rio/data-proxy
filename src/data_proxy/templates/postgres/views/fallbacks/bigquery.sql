{#
{
  "kind": "template",
  "description": "Render the private helper that prepares a fallback source view.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "app_schema": "PostgreSQL schema that holds the routing functions.",
    "function": "PostgreSQL function being created.",
    "duckdb_view": "DuckDB intermediate view name.",
    "load": "DuckDB extension load statement, e.g. 'LOAD bigquery'.",
    "source": "FROM clause body, e.g. bigquery_scan('project.dataset.table')."
  }
}
#}
-- noqa: disable=PRS,LT05
{% from "postgres/macros.sql" import column_projection %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}(p_where text, p_arg text)
RETURNS void AS $$
BEGIN
  PERFORM duckdb.raw_query(
    '{{ load }}; CREATE OR REPLACE VIEW {{ duckdb_view }} AS ' ||
    'SELECT {{ column_projection(columns) }} ' ||
    'FROM {{ source }} ' || {{ app_schema }}.and_where(p_where, p_arg)
  );
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}(text, text) FROM PUBLIC
