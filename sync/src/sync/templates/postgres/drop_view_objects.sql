{#
{
  "kind": "template",
  "description": "Drop removed table views, their table function, their source helpers, and the row of meta.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the objects.",
    "view": "View to remove.",
    "function": "Table function to remove.",
    "helpers": "List of source helper functions to remove.",
    "app_schema": "PostgreSQL schema that holds meta.",
    "schema_name": "SQL literal of the schema name.",
    "table_name": "SQL literal of the table name."
  }
}
#}
DROP VIEW IF EXISTS {{ schema }}.{{ view }};
DROP FUNCTION IF EXISTS {{ schema }}.{{ function }}();
{% for helper in helpers %}
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text, boolean, text[], text);
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text, text, boolean);
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text, boolean);
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text, text);
DROP FUNCTION IF EXISTS {{ schema }}.{{ helper }}(text, text);
{% endfor %}
DELETE FROM {{ app_schema }}.meta
WHERE schema_name = {{ schema_name }} AND table_name = {{ table_name }}
