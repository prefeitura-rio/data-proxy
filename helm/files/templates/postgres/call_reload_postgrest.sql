{#
{
  "kind": "template",
  "description": "Tell PostgREST to reload its schema and settings, after every standby replayed the change.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema that holds application state."
  }
}
#}
SELECT {{ schema }}.reload_postgrest()
