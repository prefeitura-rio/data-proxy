{#
{
  "kind": "template",
  "description": "Count the rows of one DuckLake table.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier."
  }
}
#}
SELECT count(*) AS row_count FROM dl.{{ table }}
