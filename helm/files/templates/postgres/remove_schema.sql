{#
{
  "kind": "template",
  "description": "Remove a schema that the console no longer lists, with what the engine kept for it: the state and the errors of its tables, which the names of its views identify, and the scope of its runs. The drop takes the views, the functions, access_policy and access_log with it. Run it again without an error.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier to drop.",
    "name": "SQL literal with the name of that schema.",
    "app_schema": "SQL-safe PostgreSQL schema that holds application state."
  }
}
#}
DELETE FROM {{ app_schema }}.state
WHERE split_part(table_name, '.', 3) IN (
    SELECT viewname FROM pg_views
    WHERE schemaname = {{ name }} AND viewname NOT IN ('access_policy', 'access_log')
);

DELETE FROM {{ app_schema }}.errors
WHERE split_part(fields ->> 'table', '.', 3) IN (
    SELECT viewname FROM pg_views
    WHERE schemaname = {{ name }} AND viewname NOT IN ('access_policy', 'access_log')
);

DELETE FROM {{ app_schema }}.scope
WHERE schema_name = {{ name }};

DROP SCHEMA IF EXISTS {{ schema }} CASCADE
