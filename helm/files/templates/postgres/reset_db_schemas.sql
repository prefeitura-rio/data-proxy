{#
{
  "kind": "template",
  "description": "Forget the schemas that PostgREST serves, so that its environment setting applies again.",
  "inputs": {
    "authenticator_role": "SQL-safe authenticator role identifier."
  }
}
#}
ALTER ROLE {{ authenticator_role }} RESET pgrst.db_schemas
