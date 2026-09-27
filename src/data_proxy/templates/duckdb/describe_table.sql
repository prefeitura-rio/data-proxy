{#
{
  "kind": "template",
  "description": "Describe an existing DuckLake table.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier."
  }
}
#}
-- noqa: disable=PRS
DESCRIBE dl.{{ table }}
