{#
{
  "kind": "macro",
  "name": "helm.grant_schema_usage",
  "description": "Render a PostgreSQL schema usage grant.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "role": "SQL-safe PostgreSQL role identifier."
  },
  "returns": "One PostgreSQL GRANT statement."
}
#}
{% macro grant_schema_usage(schema, role) -%}
GRANT USAGE ON SCHEMA {{ schema }} TO {{ role }};
{%- endmacro %}
