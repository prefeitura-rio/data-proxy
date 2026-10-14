{#
{
  "kind": "template",
  "description": "Read the id and the document of the active configuration revision.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
SELECT
  id::text,
  document::text
FROM {{ schema }}.revisions
WHERE active;
