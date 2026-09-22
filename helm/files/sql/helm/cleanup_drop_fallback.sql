{#
{
  "kind": "template",
  "description": "Drop the BigQuery fallback view and function that belong to one table.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "table": "SQL-safe PostgreSQL table literal."
  }
}
#}
SELECT {{ schema }}.drop_fallback_objects({{ table }})
