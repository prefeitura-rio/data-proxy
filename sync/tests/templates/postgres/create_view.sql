{#
{
  "kind": "template",
  "description": "Create one single-row view.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "view": "SQL-safe view identifier."
  }
}
#}
CREATE VIEW {{ schema }}.{{ view }} AS SELECT 1 AS id
