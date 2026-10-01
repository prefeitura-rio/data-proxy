{#
{
  "kind": "template",
  "description": "Call DBOS orphaned-workflow recovery with live executor IDs.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "live_executor_ids": "SQL-safe JSON array literal of live executor IDs.",
    "grace_seconds": "Recovery grace period in seconds."
  }
}
#}
-- noqa: disable=PRS
CALL {{ schema }}.recover_orphaned_workflows(
    {{ live_executor_ids }}::jsonb,
    {{ grace_seconds }}::integer * interval '1 second'
);
