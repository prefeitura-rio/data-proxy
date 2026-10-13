{#
{
  "kind": "template",
  "description": "Delete the workflows with a prefix. Parameters: prefix.",
  "inputs": {
    "dbos_schema": "SQL-safe DBOS schema identifier."
  }
}
#}
DELETE FROM {{ dbos_schema }}.workflow_status
WHERE workflow_uuid LIKE %(prefix)s || '%%'
