{#
{
  "kind": "template",
  "description": "Write the facts of one served table to meta, which the generic functions read when they run. A second run for the same table replaces the row.",
  "inputs": {
    "app_schema": "PostgreSQL schema that holds the routing functions and meta.",
    "schema_name": "SQL literal of the schema of the table.",
    "table_name": "SQL literal of the table name.",
    "source_table": "SQL literal of the source table name, as stored in state.",
    "scope_path": "SQL literal of the ltree path of the table.",
    "claim_setting": "SQL literal of the session setting that holds the claim of the subject.",
    "data_path": "SQL literal of the S3 data path of the schema.",
    "duckdb_view": "SQL literal of the DuckDB view name that the DuckLake helper prepares.",
    "max_rows": "The most rows that an answer holds.",
    "columns": "SQL literal of the JSON list of columns, with the type and the flags of each.",
    "rls_mappings": "SQL literal of the JSON list of unit mappings.",
    "fallbacks": "SQL literal of the JSON list of fallback sources."
  }
}
#}
INSERT INTO {{ app_schema }}.meta (
  schema_name, table_name, source_table, scope_path, claim_setting,
  data_path, view, max_rows, columns, rls_mappings, fallbacks
) VALUES (
  {{ schema_name }}, {{ table_name }}, {{ source_table }}, {{ scope_path }}, {{ claim_setting }},
  {{ data_path }}, {{ duckdb_view }}, {{ max_rows }}, {{ columns }}::jsonb, {{ rls_mappings }}::jsonb, {{ fallbacks }}::jsonb
)
ON CONFLICT (schema_name, table_name) DO UPDATE SET
  source_table = excluded.source_table,
  scope_path = excluded.scope_path,
  claim_setting = excluded.claim_setting,
  data_path = excluded.data_path,
  view = excluded.view,
  max_rows = excluded.max_rows,
  columns = excluded.columns,
  rls_mappings = excluded.rls_mappings,
  fallbacks = excluded.fallbacks
