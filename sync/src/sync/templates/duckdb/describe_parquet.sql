{#
{
  "kind": "template",
  "description": "Describe a scratch Parquet file.",
  "inputs": {}
}
#}
-- noqa: disable=PRS
DESCRIBE SELECT * FROM read_parquet(?)
