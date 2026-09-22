{#
{
  "kind": "template",
  "description": "Render the access log retention database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns access_log.",
    "log_retention_days": "Days to keep rows in access_log before pruning."
  }
}
#}
DELETE FROM {{ schema }}.access_log
WHERE changed_at
    < now() - interval '{{ log_retention_days }} days';
