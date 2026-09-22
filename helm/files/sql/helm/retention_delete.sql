{#
{
  "kind": "template",
  "description": "Delete rows older than the retention window from one table.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "table": "SQL-safe PostgreSQL table identifier.",
    "column": "SQL-safe time column identifier.",
    "window": "SQL-safe PostgreSQL interval literal."
  }
}
#}
DELETE FROM {{ schema }}.{{ table }}
WHERE {{ column }} < now() - interval {{ window }}
