{#
{
  "kind": "template",
  "description": "Select the source plan of one table. Parameters: table_name, fallbacks, pinned.",
  "inputs": {
    "app_schema": "SQL-safe application schema identifier."
  }
}
#}
SELECT name, use, arg
FROM {{ app_schema }}.plan_sources(%(table_name)s, %(fallbacks)s, %(pinned)s)
