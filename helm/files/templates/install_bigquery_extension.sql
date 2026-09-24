{#
{
  "kind": "template",
  "description": "Install the BigQuery community extension for pg_duckdb fallback views."
}
#}
SELECT duckdb.install_extension('bigquery', 'community')
