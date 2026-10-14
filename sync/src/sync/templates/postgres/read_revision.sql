{#
{
  "kind": "template",
  "description": "Read the document of one configuration revision.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
SELECT document::text FROM {{ schema }}.revisions WHERE id = %(revision_id)s;
