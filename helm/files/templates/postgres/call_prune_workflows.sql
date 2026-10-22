{#
{
  "kind": "template",
  "description": "Call the prune_workflows procedure with a retention psql variable.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
CALL {{ schema }}.prune_workflows(:'retention'::interval)
