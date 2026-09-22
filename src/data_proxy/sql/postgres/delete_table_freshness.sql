{#
{
  "kind": "template",
  "description": "Render the delete table freshness database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects."
  }
}
#}
DELETE FROM {{ schema }}.freshness
WHERE "table" = %s
