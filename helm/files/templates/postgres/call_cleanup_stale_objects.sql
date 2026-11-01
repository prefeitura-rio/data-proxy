{#
{
  "kind": "template",
  "description": "Call the cleanup_stale_objects maintenance procedure with a config JSONB variable.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "schema_argument": "SQL-safe schema filter argument."
  }
}
#}
CALL {{ schema }}.cleanup_stale_objects(:'config'::jsonb, {{ schema_argument }})
