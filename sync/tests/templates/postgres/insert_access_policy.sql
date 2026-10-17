{#
{
  "kind": "template",
  "description": "Insert one access-policy grant. Parameters: subject, unit_type, unit_id.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
INSERT INTO {{ schema }}.access_policy (subject, unit_type, unit_id)
VALUES (%(subject)s, %(unit_type)s, %(unit_id)s)
