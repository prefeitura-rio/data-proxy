{#
{
  "kind": "template",
  "description": "Describe a scratch Parquet file.",
  "inputs": {}
}
#}
-- noqa: disable=PRS
-- The SQLFluff DuckDB dialect can't parse this statement.
DESCRIBE SELECT * FROM read_parquet(?)
