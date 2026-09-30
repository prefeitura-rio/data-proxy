{#
{
  "kind": "template",
  "description": "Select the visible multi-mapping rows.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
SELECT
    region_id,
    group_id
FROM {{ schema }}.multi_visible
ORDER BY region_id, group_id
