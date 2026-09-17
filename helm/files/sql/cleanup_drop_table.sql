{#
{
  "kind": "template",
  "description": "Drop one stale table and its dependent objects.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "table": "SQL-safe PostgreSQL table identifier."
  }
}
#}
DROP TABLE IF EXISTS {{ schema }}.{{ table }} CASCADE
