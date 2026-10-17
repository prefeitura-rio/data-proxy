{#
{
  "kind": "template",
  "description": "Drop the metadata schema of a removed schema in the catalog database. DuckLake keeps its tables there. Run it again without an error.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier of the catalog."
  }
}
#}
DROP SCHEMA IF EXISTS {{ schema }} CASCADE
