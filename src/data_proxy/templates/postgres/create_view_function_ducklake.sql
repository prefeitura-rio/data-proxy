{#
{
  "kind": "template",
  "description": "Render a SECURITY DEFINER query function backed by a DuckLake catalog.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "function": "PostgreSQL function being created or called.",
    "columns": "Structured SQL-safe column metadata.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "has_rls": "Enable the row-level security branch.",
    "rls_mappings": "Unit mappings used to build the access-policy predicate.",
    "duckdb_view": "DuckDB intermediate view name.",
    "source": "FROM clause body, e.g. dl.table.",
    "catalog_local_path": "Local filesystem path to the SQLite catalog file.",
    "data_path": "S3 data path for the DuckLake attachment."
  }
}
#}
-- noqa: disable=PRS,LT05
{% from "postgres/macros.sql" import rls_where_clause, column_projection, return_query_select, duckdb_query_select, ducklake_attach %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}()
RETURNS TABLE(
{% for column in columns %}
{{ column.name }} {{ column.return_type }}{% if not loop.last %}, {% endif %}
{% endfor %}
) AS $$
DECLARE
  v_subject text;
  v_where text;
BEGIN
{{ ducklake_attach(catalog_local_path, data_path) }}

{% if has_rls == "true" %}
  v_subject := current_setting('{{ claim_setting }}', true);

{{ rls_where_clause(schema, has_rls, rls_mappings) }}

  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW {{ duckdb_view }} AS ' ||
    'SELECT {{ column_projection(columns) }} ' ||
    'FROM {{ source }} ' || v_where
  );

  RETURN QUERY
{{ return_query_select(columns) }}
{{ duckdb_query_select(columns, duckdb_view) }};
{% else %}
  RETURN QUERY
{{ return_query_select(columns) }}
    FROM duckdb.query('SELECT {{ column_projection(columns) }} FROM {{ source }}') r;
{% endif %}
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
