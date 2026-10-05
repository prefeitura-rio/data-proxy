{#
{
  "kind": "template",
  "description": "Drop removed table views and their query and helper functions.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the objects.",
    "view": "View to remove.",
    "function": "Table function to remove.",
    "helpers": "List of source helper functions to remove."
  }
}
#}
DROP VIEW IF EXISTS {{ schema }}.{{ view }};
DROP FUNCTION IF EXISTS {{ schema }}.{{ function }}();
{% for helper in helpers %}
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text);
{% endfor %}
