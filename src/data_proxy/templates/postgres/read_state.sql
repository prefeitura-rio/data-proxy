{#
{
  "kind": "template",
  "description": "Read committed state for one table from data-proxy.state.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (default data-proxy)."
  }
}
#}
SELECT state::text FROM {{ schema }}.state WHERE table_name = %(table_name)s;
