{#
{
  "kind": "template",
  "description": "Describe a BigQuery table schema through DuckDB.",
  "inputs": {
    "bq_table": "BigQuery table reference used by DuckDB."
  }
}
#}
-- noqa: PRS
DESCRIBE SELECT * FROM bigquery_scan({{ bq_table }})
