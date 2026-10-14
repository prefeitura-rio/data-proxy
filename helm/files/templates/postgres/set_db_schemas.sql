{#
{
  "kind": "template",
  "description": "Set the schemas that PostgREST serves, as a setting of the authenticator role.",
  "inputs": {
    "authenticator_role": "SQL-safe authenticator role identifier.",
    "db_schemas": "SQL-safe literal with the comma-separated schema names."
  }
}
#}
ALTER ROLE {{ authenticator_role }} SET pgrst.db_schemas = {{ db_schemas }}
