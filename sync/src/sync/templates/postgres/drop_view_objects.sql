{#
{
  "kind": "template",
  "description": "Drop removed table views and their query and helper functions.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the objects.",
    "view": "View to remove.",
    "function": "Table function to remove.",
    "aggregate_function": "Aggregate function to remove. An empty text if the table name is too long for one.",
    "helpers": "List of source helper functions to remove."
  }
}
#}
DROP VIEW IF EXISTS {{ schema }}.{{ view }};
DROP FUNCTION IF EXISTS {{ schema }}.{{ function }}();
{% if aggregate_function %}
DROP FUNCTION IF EXISTS {{ schema }}.{{ aggregate_function }}();
{% endif %}
{% for helper in helpers %}
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text, boolean, text[], text);
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text, text, boolean);
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text, boolean);
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text, text);
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text);
{% endfor %}
