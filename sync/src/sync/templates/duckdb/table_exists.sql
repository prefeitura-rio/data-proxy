{#
{
  "kind": "template",
  "description": "Check whether a DuckLake table exists.",
  "inputs": {}
}
#}
-- noqa: disable=PRS
SELECT count(*)
FROM __ducklake_metadata_dl.ducklake_table
WHERE table_name = ?
