{#
{
  "kind": "template",
  "description": "Record the schemas that a run and its dump tasks cover, so the console can scope them.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
INSERT INTO {{ schema }}.scope (workflow_id, run_id, schema_name, table_name)
SELECT r.workflow_id, r.run_id, r.schema_name, r.table_name
FROM jsonb_to_recordset(%(rows)s::jsonb) AS r (
    workflow_id text, run_id text, schema_name text, table_name text
)
ON CONFLICT DO NOTHING;
