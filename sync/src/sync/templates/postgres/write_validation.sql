{#
{
  "kind": "template",
  "description": "Record the validation result of a revision, only while it still holds the checked document.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
UPDATE {{ schema }}.revisions
SET
  valid = %(valid)s,
  errors = %(errors)s::jsonb,
  validated_at = now()
WHERE id = %(revision_id)s AND document = %(document)s::jsonb
RETURNING id;
