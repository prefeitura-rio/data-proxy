{#
{
  "kind": "template",
  "description": "Insert a scratch Parquet file into a DuckLake table by column name.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier."
  }
}
#}
-- noqa: disable=PRS,AM04
INSERT INTO dl.{{ table }} BY NAME
SELECT * FROM read_parquet(?)
