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
-- The SQLFluff DuckDB dialect can't parse this statement.
DESCRIBE dl.{{ table }}
