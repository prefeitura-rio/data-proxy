{#
{
  "kind": "template",
  "description": "Load the DuckDB BigQuery extension in the pg_duckdb session."
}
#}
SELECT duckdb.raw_query('LOAD bigquery;')
