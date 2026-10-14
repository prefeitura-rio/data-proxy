{#
{
  "kind": "template",
  "description": "Insert one access-log row with an age. Parameters: subject, unit_type, unit_id, action, age.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
INSERT INTO {{ schema }}.access_log (subject, unit_type, unit_id, action, changed_at)
VALUES (
    %(subject)s, %(unit_type)s, %(unit_id)s, %(action)s, now() - %(age)s::interval
)
