{#
{
  "kind": "template",
  "description": "Insert one DBOS workflow with a status and a last update time. Parameters: workflow_uuid, status, updated_at.",
  "inputs": {
    "dbos_schema": "SQL-safe DBOS schema identifier."
  }
}
#}
INSERT INTO {{ dbos_schema }}.workflow_status (
    workflow_uuid, status, updated_at
) VALUES (
    %(workflow_uuid)s, %(status)s, %(updated_at)s
)
