{#
{
  "kind": "template",
  "description": "Check whether a DuckLake table exists.",
  "inputs": {}
}
#}
-- noqa: disable=PRS
-- The SQLFluff DuckDB dialect can't parse this statement.
SELECT count(*)
FROM duckdb_tables()
WHERE database_name = 'dl' AND table_name = ?
