{#
{
  "kind": "template",
  "description": "Remove every access-policy row for one PostgreSQL schema through DELETE so access_log records it.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
DELETE FROM {{ schema }}.access_policy
