{#
{
  "kind": "template",
  "description": "Create a PostgreSQL view that wraps a query function with column projection.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "view": "PostgreSQL view being created.",
    "function": "PostgreSQL function being called.",
    "columns": "SQL-safe column expressions for the view projection."
  }
}
#}
-- noqa: disable=LT05
CREATE OR REPLACE VIEW {{ schema }}.{{ view }} AS
SELECT
{% for column in columns %}
  {{ column }}{% if not loop.last %},{% endif %}
{% endfor %}
FROM {{ schema }}.{{ function }}()
