{#
{
  "kind": "template",
  "description": "Create one table with text columns.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "table": "SQL-safe table identifier.",
    "columns": "SQL-safe column identifiers."
  }
}
#}
CREATE TABLE {{ schema }}.{{ table }} (
{% for column in columns %}
    {{ column }} text{% if not loop.last %},{% endif %}

{% endfor %}
)
