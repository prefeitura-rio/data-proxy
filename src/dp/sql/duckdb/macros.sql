
{#
{
  "kind": "macro",
  "name": "duckdb.select_projection",
  "description": "Render a DuckDB projection that converts nested and JSON columns to JSON.",
  "inputs": {
    "json_columns": "SQL-safe identifiers for nested or JSON columns."
  },
  "returns": "A SELECT projection expression."
}
#}
{% macro select_projection(json_columns) -%}
SELECT *{% if json_columns %} REPLACE ({% for column in json_columns %}to_json({{ column }}) AS {{ column }}{% if not loop.last %}, {% endif %}{% endfor %}){% endif %}
{%- endmacro %}
