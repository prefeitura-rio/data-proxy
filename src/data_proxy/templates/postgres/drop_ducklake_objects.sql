{#
{
  "kind": "template",
  "description": "Drop removed DuckLake and BigQuery views and query functions.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the objects.",
    "view": "View to remove.",
    "function": "DuckLake function to remove.",
    "bq_function": "BigQuery function to remove."
  }
}
#}
DROP VIEW IF EXISTS {{ schema }}.{{ view }};
DROP VIEW IF EXISTS {{ schema }}.{{ view }}_bq;
DROP FUNCTION IF EXISTS {{ schema }}.{{ function }}();
DROP FUNCTION IF EXISTS {{ schema }}.{{ bq_function }}();
