{#
{
  "kind": "template",
  "description": "Create one application schema when it is absent.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }}
