{#
{
  "kind": "template",
  "description": "Delete the state and the errors of every table that the console no longer lists.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
-- noqa: disable=PRS
WITH removed_state AS (
    DELETE FROM {{ schema }}.state
    WHERE table_name NOT IN (SELECT jsonb_array_elements_text(%(kept)s::jsonb))
)

DELETE FROM {{ schema }}.errors
WHERE fields ->> 'table' NOT IN (SELECT jsonb_array_elements_text(%(kept)s::jsonb));
