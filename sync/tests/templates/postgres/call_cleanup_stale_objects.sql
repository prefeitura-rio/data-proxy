{#
{
  "kind": "template",
  "description": "Call the cleanup procedure. Parameters: config, schema_name.",
  "inputs": {
    "app_schema": "SQL-safe application schema identifier."
  }
}
#}
CALL {{ app_schema }}.cleanup_stale_objects(%(config)s::jsonb, %(schema_name)s)
