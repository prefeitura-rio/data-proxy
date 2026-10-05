{#
{
  "kind": "template",
  "description": "Describe an ingestion source table through DuckDB from PostgreSQL.",
  "inputs": {
    "source": "Source-generated DuckDB FROM expression."
  }
}
#}
SELECT *
FROM duckdb.query(
    $duck$
    DESCRIBE SELECT * FROM {{ source }}
    $duck$
)
