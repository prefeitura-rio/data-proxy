{#
{
  "kind": "template",
  "description": "Tell PostgREST to reload its schema, after every standby replayed the change.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
SELECT {{ schema }}.reload_postgrest();
