{#
{
  "kind": "template",
  "description": "Render the write remainder database operation.",
  "inputs": {
    "json_columns": "SQL-safe identifiers for nested or JSON columns.",
    "bq_table": "BigQuery table reference used by DuckDB.",
    "column": "SQL-safe identifier for the partition or source column.",
    "lower": "Inclusive lower partition bound.",
    "upper": "Exclusive upper partition bound.",
    "path": "SQL-safe Parquet or object-storage path literal."
  }
}
#}
{% from "duckdb/macros.sql" import select_projection %}
COPY (
    {{ select_projection(json_columns) }} FROM bigquery_scan({{ bq_table }})
    WHERE
        {{ column }} IS NULL
        OR {{ column }} < {{ lower }}
        OR {{ column }} >= {{ upper }}
) TO {{ path }} (FORMAT PARQUET)
