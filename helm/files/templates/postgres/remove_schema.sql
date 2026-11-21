{#
{
  "kind": "template",
  "description": "Remove a schema that the console no longer lists, with the state, the errors and the run scope that the engine kept for it.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier to drop.",
    "name": "SQL literal with the name of that schema.",
    "app_schema": "SQL-safe PostgreSQL schema that holds application state."
  }
}
#}
{#
The drop takes the views and the functions with it.
The delete takes the grants of access.policy with it, those on the schema and those on its tables.
The access log keeps its rows.
Run it again without an error.
#}
DELETE FROM {{ app_schema }}.state
WHERE split_part(table_name, '.', 3) IN (
    SELECT viewname FROM pg_views
    WHERE schemaname = {{ name }}
);

DELETE FROM {{ app_schema }}.errors
WHERE split_part(fields ->> 'table', '.', 3) IN (
    SELECT viewname FROM pg_views
    WHERE schemaname = {{ name }}
);

DELETE FROM {{ app_schema }}.scope
WHERE schema_name = {{ name }};

DELETE FROM access.policy
WHERE scope <@ text2ltree(replace({{ name }}, '__', '.'));

DROP SCHEMA IF EXISTS {{ schema }} CASCADE
