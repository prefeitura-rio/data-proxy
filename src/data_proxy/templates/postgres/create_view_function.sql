{#
{
  "kind": "template",
  "description": "Render a SECURITY DEFINER query function backed by DuckLake or BigQuery fallback.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "function": "PostgreSQL function being created or called.",
    "columns": "Structured SQL-safe column metadata.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "scope": "SQL predicate limiting access to the current schema.",
    "has_rls": "Enable the row-level security branch.",
    "rls_mappings": "Unit mappings used to build the access-policy predicate.",
    "duckdb_view": "DuckDB intermediate view name.",
    "source": "FROM clause body: bigquery_scan(...) or dl.table.",
    "source_prefix": "Optional string prepended to the raw query (e.g. 'LOAD bigquery; ').",
    "catalog_local_path": "Local SQLite catalog path (DuckLake only).",
    "data_path": "S3 data path for the DuckLake attachment (DuckLake only)."
  }
}
#}
{% from "postgres/macros.sql" import rls_where_clause, column_projection, return_query_select, duckdb_query_select %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}()
RETURNS TABLE(
{% for column in columns %}
{{ column.name }} {{ column.return_type }}{% if not loop.last %}, {% endif %}
{% endfor %}
) AS $$
DECLARE
  v_subject text;
  v_schemas text;
  v_is_admin boolean;
  v_unit_ids text[];
  v_where text;
{% if catalog_local_path is defined %}
  catalog_revision text;
  attached_revision text;
{% endif %}
BEGIN
{% if catalog_local_path is defined %}
  SELECT coalesce(to_char(max(updated_at), 'YYYY-MM-DD"T"HH24:MI:SS.USOF'), '0')
  INTO catalog_revision
  FROM {{ schema }}.freshness;
  attached_revision := current_setting(
    'app.ducklake_revision_{{ schema }}', true
  );

  IF attached_revision IS DISTINCT FROM catalog_revision THEN
    PERFORM duckdb.raw_query('CALL duckdb.recycle_ddb()');
    PERFORM duckdb.raw_query(
      'ATTACH ''ducklake:sqlite:{{ catalog_local_path }}'' AS dl '
      || '(DATA_PATH ''{{ data_path }}'', READ_ONLY)'
    );
    PERFORM set_config(
      'app.ducklake_revision_{{ schema }}', catalog_revision, false
    );
  END IF;

{% endif %}
  v_subject := current_setting('{{ claim_setting }}', true);
  v_schemas := current_setting('app.claim_schemas', true);

  IF NOT ({{ scope }}) THEN
    RETURN;
  END IF;

  SELECT EXISTS(
    SELECT 1 FROM {{ schema }}.access_policy p
    WHERE p.subject = v_subject AND p.is_admin
  ) INTO v_is_admin;

{{ rls_where_clause(schema, has_rls, rls_mappings) }}

  PERFORM duckdb.raw_query(
{% if source_prefix is defined %}    '{{ source_prefix }}' ||
{% endif %}    'CREATE OR REPLACE VIEW {{ duckdb_view }} AS ' ||
    'SELECT {{ column_projection(columns) }} ' ||
    'FROM {{ source }} ' || v_where
  );

  RETURN QUERY
{{ return_query_select(columns) }}
{{ duckdb_query_select(columns, duckdb_view) }};
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
