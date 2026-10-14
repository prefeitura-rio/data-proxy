{#
{
  "kind": "template",
  "description": "Select the status of the workflows with a prefix. Parameters: prefix.",
  "inputs": {
    "dbos_schema": "SQL-safe DBOS schema identifier."
  }
}
#}
SELECT workflow_uuid, status, started_at_epoch_ms IS NULL AS restarted
FROM {{ dbos_schema }}.workflow_status
WHERE workflow_uuid LIKE %(prefix)s || '%%'
ORDER BY workflow_uuid
