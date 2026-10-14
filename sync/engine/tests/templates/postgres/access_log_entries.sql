{#
{
  "kind": "template",
  "description": "Select access-log rows in change order.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
SELECT
    subject,
    unit_type,
    unit_id,
    action
FROM {{ schema }}.access_log
ORDER BY changed_at
