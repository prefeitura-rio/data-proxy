{#
{
  "kind": "template",
  "description": "Check whether a DuckLake table exists.",
  "inputs": {}
}
#}
-- noqa: disable=PRS
SELECT count(*)
FROM duckdb_tables()
WHERE database_name = 'dl' AND table_name = ?
