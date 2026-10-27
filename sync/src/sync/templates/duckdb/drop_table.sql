{#
{
  "kind": "template",
  "description": "Drop a DuckLake table. Run it again without an error.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier."
  }
}
#}
-- noqa: disable=PRS
DROP TABLE IF EXISTS dl.{{ table }}
