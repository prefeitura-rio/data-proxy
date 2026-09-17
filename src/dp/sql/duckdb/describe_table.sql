{#
{
  "kind": "template",
  "description": "Render the describe table database operation.",
  "inputs": {
    "bq_table": "BigQuery table reference used by DuckDB."
  }
}
#}
-- noqa: PRS
DESCRIBE SELECT * FROM bigquery_scan({{ bq_table }})
