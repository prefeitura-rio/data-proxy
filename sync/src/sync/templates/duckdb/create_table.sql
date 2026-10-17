{#
{
  "kind": "template",
  "description": "Create a DuckLake table from a scratch Parquet schema.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier."
  }
}
#}
-- noqa: disable=PRS,AM04
CREATE TABLE IF NOT EXISTS dl.{{ table }} AS
SELECT * FROM read_parquet(?)
LIMIT 0
