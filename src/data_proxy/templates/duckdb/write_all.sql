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
{% from "duckdb/macros.sql" import select_projection %}
COPY (
    {{ select_projection(json_columns) }}
    FROM bigquery_scan({{ bq_table }})
) TO {{ path }} (
    FORMAT PARQUET
)
