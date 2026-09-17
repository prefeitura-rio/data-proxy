{#
{
  "kind": "template",
  "description": "Render the write all database operation.",
  "inputs": {
    "json_columns": "SQL-safe identifiers for nested or JSON columns.",
    "bq_table": "BigQuery table reference used by DuckDB.",
    "path": "SQL-safe Parquet or object-storage path literal."
  }
}
#}
COPY (
    SELECT *{% if json_columns %} REPLACE ({% for column in json_columns %}to_json({{ column }}) AS {{ column }}{% if not loop.last %}, {% endif %}{% endfor %}){% endif %}
    FROM bigquery_scan({{ bq_table }})
) TO {{ path }} (
    FORMAT PARQUET
)
