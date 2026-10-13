{#
{
  "kind": "template",
  "description": "Select the visible scoped rows.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
SELECT id FROM {{ schema }}.scoped
