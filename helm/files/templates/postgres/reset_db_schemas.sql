{#
{
  "kind": "template",
  "description": "Forget the schemas that PostgREST serves, so that its environment setting applies again.",
  "inputs": {
    "serving_role": "SQL-safe identifier of the serving login."
  }
}
#}
ALTER ROLE {{ serving_role }} RESET pgrst.db_schemas
