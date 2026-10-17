{#
{
  "kind": "template",
  "description": "Set the schemas that PostgREST serves, as a setting of the serving login.",
  "inputs": {
    "serving_role": "SQL-safe identifier of the serving login.",
    "db_schemas": "SQL-safe literal with the comma-separated schema names."
  }
}
#}
ALTER ROLE {{ serving_role }} SET pgrst.db_schemas = {{ db_schemas }}
