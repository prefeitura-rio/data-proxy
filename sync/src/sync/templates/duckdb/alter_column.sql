{#
{
  "kind": "template",
  "description": "Apply one or more column alterations to a DuckLake table in a single statement.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier.",
    "alterations": "List of {operation, column, type} where operation is add, drop, or promote."
  }
}
#}
-- noqa: disable=PRS,LT05
ALTER TABLE dl.{{ table }}
{% for alteration in alterations %}{% if alteration.operation == "add" %} ADD COLUMN {{ alteration.column }} {{ alteration.type }}{% elif alteration.operation == "drop" %} DROP COLUMN {{ alteration.column }}{% else %} ALTER COLUMN {{ alteration.column }} SET DATA TYPE {{ alteration.type }}{% endif %}{% if not loop.last %},{% endif %}
{% endfor %}
