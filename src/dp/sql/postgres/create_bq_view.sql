{#
{
  "kind": "template",
  "description": "Render the create bq view database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "view": "PostgreSQL view being created or granted.",
    "column": "SQL-safe identifier for the partition or source column.",
    "function": "PostgreSQL function being created or called."
  }
}
#}
-- noqa: disable=LT05
CREATE OR REPLACE VIEW {{ schema }}.{{ view }} AS
SELECT {% for column in columns %}{{ column }}{% if not loop.last %}, {% endif %}{% endfor %}
FROM {{ schema }}.{{ function }}()
