{#
{
  "kind": "template",
  "description": "Delete freshness rows for one table, optionally scoped to one partition and strategy.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the freshness table."
  }
}
#}
DELETE FROM {{ schema }}.freshness
WHERE "table" = %s
{% if partition is defined %}  AND strategy = %s
  AND partition = %s{% endif %}
