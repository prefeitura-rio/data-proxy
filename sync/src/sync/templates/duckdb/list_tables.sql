{#
{
  "kind": "template",
  "description": "List the tables of the attached DuckLake catalog.",
  "inputs": {}
}
#}
-- noqa: disable=PRS
SELECT table_name
FROM duckdb_tables()
WHERE database_name = 'dl'
