{#
{
  "kind": "template",
  "description": "Render the delete all rows database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed."
  }
}
#}
DELETE FROM {{ schema }}.{{ table }}
