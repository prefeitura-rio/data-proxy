{#
{
  "kind": "template",
  "description": "Create a procedure that re-enqueues DBOS workflows owned by terminated sync pods.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the procedure.",
    "application_name": "SQL-safe DBOS application name literal."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE OR REPLACE PROCEDURE {{ schema }}.recover_orphaned_workflows(
    p_live_executor_ids jsonb,
    p_grace interval
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, dbos, pg_temp
AS $$
DECLARE
    recovered_count integer;
BEGIN
    UPDATE dbos.workflow_status AS workflow
    SET
        status = 'ENQUEUED',
        started_at_epoch_ms = NULL,
        updated_at = (EXTRACT(epoch FROM clock_timestamp()) * 1000)::bigint
    WHERE workflow.status = 'PENDING'
      AND workflow.application_name = {{ application_name }}
      AND workflow.queue_name IS NOT NULL
      AND workflow.executor_id IS NOT NULL
      AND workflow.started_at_epoch_ms < (
          EXTRACT(epoch FROM clock_timestamp() - p_grace) * 1000
      )::bigint
      AND NOT EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(p_live_executor_ids) AS live(executor_id)
          WHERE live.executor_id = workflow.executor_id
      );

    GET DIAGNOSTICS recovered_count = ROW_COUNT;
    RAISE NOTICE 'Recovered % DBOS workflows', recovered_count;
END;
$$;
REVOKE ALL ON PROCEDURE {{ schema }}.recover_orphaned_workflows(jsonb, interval)
FROM public;
