{#
{
  "kind": "template",
  "description": "Change the unit of one subject's grants. Parameters: subject, unit_id.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
UPDATE {{ schema }}.access_policy
SET unit_id = %(unit_id)s
WHERE subject = %(subject)s
