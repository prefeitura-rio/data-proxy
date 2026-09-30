{#
{
  "kind": "template",
  "description": "Select the visible regions.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
SELECT region_id FROM {{ schema }}.visible
ORDER BY region_id
