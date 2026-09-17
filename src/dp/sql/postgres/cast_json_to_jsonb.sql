{#
{
  "kind": "template",
  "description": "Render the cast json to jsonb database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed.",
    "column": "SQL-safe identifier for the partition or source column."
  }
}
#}
ALTER TABLE {{ schema }}.{{ table }}
{% for column in columns %}
    ALTER COLUMN {{ column }}
    SET DATA TYPE jsonb
    USING {{ column }}::jsonb{% if not loop.last %}, {% endif %}
{% endfor %}
