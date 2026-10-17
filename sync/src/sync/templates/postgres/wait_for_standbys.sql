{#
{
  "kind": "template",
  "description": "Wait until every streaming standby replayed what the primary has written.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
SELECT {{ schema }}.wait_for_standbys();
