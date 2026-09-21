{#
{
  "kind": "template",
  "description": "Render the install bigquery extension database operation."
}
#}
SELECT duckdb.install_extension('bigquery', 'community')
