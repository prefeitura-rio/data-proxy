{#
{
  "kind": "template",
  "description": "Render the private helper that reads the rows a fallback source owns.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "app_schema": "PostgreSQL schema that holds the routing functions.",
    "function": "PostgreSQL function being created.",
    "columns": "Structured SQL-safe column metadata.",
    "duckdb_view": "DuckDB intermediate view name.",
    "load": "DuckDB extension load statement, e.g. 'LOAD bigquery'.",
    "source": "FROM clause body, e.g. bigquery_scan('project.dataset.table')."
  }
}
#}
-- noqa: disable=PRS,LT05
{% from "postgres/macros.sql" import column_projection, return_query_select, duckdb_query_select %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}(p_where text, p_arg text)
RETURNS TABLE(
{% for column in columns %}
{{ column.name }} {{ column.return_type }}{% if not loop.last %}, {% endif %}
{% endfor %}
) AS $$
BEGIN
  PERFORM duckdb.raw_query(
    '{{ load }}; CREATE OR REPLACE VIEW {{ duckdb_view }} AS ' ||
    'SELECT {{ column_projection(columns) }} ' ||
    'FROM {{ source }} ' || {{ app_schema }}.and_where(p_where, p_arg)
  );

  RETURN QUERY
{{ return_query_select(columns) }}
{{ duckdb_query_select(columns, duckdb_view) }};
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}(text, text) FROM PUBLIC
