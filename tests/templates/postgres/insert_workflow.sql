{#
{
  "kind": "template",
  "description": "Insert one pending DBOS workflow. Parameters: workflow_uuid, application_name, queue_name, executor_id, started_at_epoch_ms.",
  "inputs": {
    "dbos_schema": "SQL-safe DBOS schema identifier."
  }
}
#}
INSERT INTO {{ dbos_schema }}.workflow_status (
    workflow_uuid, status, application_name, queue_name, executor_id,
    started_at_epoch_ms
) VALUES (
    %(workflow_uuid)s, 'PENDING', %(application_name)s, %(queue_name)s,
    %(executor_id)s, %(started_at_epoch_ms)s
)
