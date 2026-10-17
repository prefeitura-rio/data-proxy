{#
{
  "kind": "template",
  "description": "Copy one source partition range to Parquet on S3.",
  "inputs": {
    "json_columns": "SQL-safe identifiers for nested or JSON columns.",
    "source": "Source-generated DuckDB FROM expression.",
    "column": "SQL-safe identifier for the partition or source column.",
    "lower": "Inclusive lower partition bound.",
    "upper": "Exclusive upper partition bound.",
    "path": "SQL-safe Parquet or object-storage path literal."
  }
}
#}
{% from "duckdb/macros.sql" import select_projection %}
COPY (
    {{ select_projection(json_columns) }} FROM {{ source }}
    WHERE
        {{ column }} >= {{ lower }}
        AND {{ column }} < {{ upper }}
) TO {{ path }} (FORMAT PARQUET)
