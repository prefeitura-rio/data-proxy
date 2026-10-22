{#
{
  "kind": "template",
  "description": "Read committed state for one table from data-proxy.state.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
SELECT state::text FROM {{ schema }}.state WHERE table_name = %(table_name)s;
