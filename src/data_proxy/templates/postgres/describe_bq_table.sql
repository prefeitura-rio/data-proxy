{#
{
  "kind": "template",
  "description": "Describe a BigQuery table through DuckDB from PostgreSQL.",
  "inputs": {
    "bq_table": "BigQuery table reference used by DuckDB."
  }
}
#}
SELECT *
FROM duckdb.query(
    $duck$
    DESCRIBE SELECT * FROM bigquery_scan({{ bq_table }})
    $duck$
)
