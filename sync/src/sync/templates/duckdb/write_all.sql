{#
{
  "kind": "template",
  "description": "Copy an entire ingestion-source table to Parquet on S3.",
  "inputs": {
    "json_columns": "SQL-safe identifiers for nested or JSON columns.",
    "source": "Source-generated DuckDB FROM expression.",
    "path": "SQL-safe Parquet or object-storage path literal."
  }
}
#}
{% from "duckdb/macros.sql" import select_projection %}
COPY (
    {{ select_projection(json_columns) }}
    FROM {{ source }}
) TO {{ path }} (
    FORMAT PARQUET
)
