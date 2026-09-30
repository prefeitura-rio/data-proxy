{#
{
  "kind": "template",
  "description": "Select the subjects of the visible grants.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
SELECT subject FROM {{ schema }}.access_policy
ORDER BY subject
