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

{#
{
  "kind": "macro",
  "name": "duckdb.parquet_options",
  "description": "Render the options of the Parquet files that a dump writes. A row group is in memory while DuckDB writes it, and the default of 122880 rows needs more than 2 GB for a table with wide JSON columns. A smaller row group keeps the memory of a dump near 1 GB. RETURN_STATS makes the COPY return the rows and the size of each file, which the dump logs.",
  "inputs": {},
  "returns": "The options of a COPY to Parquet."
}
#}
{% macro parquet_options() -%}
FORMAT PARQUET, ROW_GROUP_SIZE 10000, RETURN_STATS
{%- endmacro %}
