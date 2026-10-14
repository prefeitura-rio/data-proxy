{#
{
  "kind": "template",
  "description": "Delete one subject's grants. Parameters: subject.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
DELETE FROM {{ schema }}.access_policy WHERE subject = %(subject)s
