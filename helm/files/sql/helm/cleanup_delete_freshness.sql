{#
{
  "kind": "template",
  "description": "Delete freshness state for one stale table.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "table": "SQL-safe PostgreSQL table literal."
  }
}
#}
DELETE FROM {{ schema }}.freshness
WHERE "table" = {{ table }}
