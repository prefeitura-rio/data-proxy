{#
{
  "kind": "template",
  "description": "Delete committed table state for one stale table from dp.table_state.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier for application state.",
    "table": "SQL-safe PostgreSQL table literal."
  }
}
#}
DELETE FROM {{ schema }}.table_state
WHERE table_name = {{ table }}
