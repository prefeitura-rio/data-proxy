{#
{
  "kind": "template",
  "description": "Render the delete partition freshness database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects."
  }
}
#}
DELETE FROM {{ schema }}.freshness
WHERE "table" = %s
  AND strategy = %s
  AND partition = %s
