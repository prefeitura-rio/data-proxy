{#
{
  "kind": "template",
  "description": "Render a read-only DuckLake table change-feed function.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the function.",
    "function": "PostgreSQL function being created.",
    "table_name": "SQL literal table name passed to dl.table_changes.",
    "duckdb_view": "DuckDB intermediate view name.",
    "columns": "Structured SQL-safe column metadata.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "scope": "SQL predicate limiting access to the current schema.",
    "has_rls": "Enable the row-level security branch.",
    "rls_mappings": "Unit mappings used to build the access-policy predicate.",
    "catalog_local_path": "Local filesystem path to the SQLite catalog file.",
    "data_path": "S3 data path for the DuckLake attachment.",
    "user_role": "Role allowed to execute the function."
  }
}
#}
-- noqa: disable=PRS
{% from "postgres/macros.sql" import
  rls_where_clause, column_projection, ducklake_attach %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}(
  start_snapshot bigint,
  end_snapshot bigint DEFAULT NULL
)
RETURNS TABLE(
  change_snapshot_id bigint,
  change_row_id bigint,
  change_type text,
{% for column in columns %}
  {{ column.name }} {{ column.return_type }}{% if not loop.last %},{% endif %}
{% endfor %}
) AS $$
DECLARE
  v_subject text;
  v_schemas text;
  v_is_admin boolean;
  v_unit_ids text[];
  v_where text;
  v_end_snapshot bigint;
BEGIN
{{ ducklake_attach(catalog_local_path, data_path) }}

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

  IF end_snapshot IS NULL THEN
    SELECT r['id']::bigint INTO v_end_snapshot
    FROM duckdb.query('SELECT id FROM dl.current_snapshot()') r;
  ELSE
    v_end_snapshot := end_snapshot;
  END IF;

  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW {{ duckdb_view }} AS '
    || 'SELECT snapshot_id AS change_snapshot_id, '
    || 'rowid AS change_row_id, change_type, '
    || '{{ column_projection(columns) }} '
    || 'FROM dl.table_changes({{ table_name }}, '
    || start_snapshot::text || ', ' || v_end_snapshot::text || ') ' || v_where
  );

  RETURN QUERY
  SELECT
    r['change_snapshot_id']::bigint,
    r['change_row_id']::bigint,
    r['change_type']::text,
{% for column in columns %}
    r[{{ column.key }}]::{{ 'text' if column.is_json else column.pg_type }} AS {{ column.name }}{% if not loop.last %},{% endif %}
{% endfor %}
  FROM duckdb.query(
    'SELECT change_snapshot_id, change_row_id, change_type, '
    || '{{ column_projection(columns) }} '
    || 'FROM {{ duckdb_view }}'
  ) r;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

GRANT EXECUTE ON FUNCTION {{ schema }}.{{ function }}(bigint, bigint) TO {{ user_role }};
