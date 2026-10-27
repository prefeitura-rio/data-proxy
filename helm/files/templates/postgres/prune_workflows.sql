{#
{
  "kind": "template",
  "description": "Create a procedure that deletes the DBOS workflows that finished before the retention interval, and the scope rows of the workflows that no longer exist.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the procedure and the scope table."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE OR REPLACE PROCEDURE {{ schema }}.prune_workflows(p_retention interval)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, dbos, pg_temp
AS $$
DECLARE
    pruned_count integer;
BEGIN
    DELETE FROM dbos.workflow_status AS workflow
    WHERE workflow.status IN ('SUCCESS', 'ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED')
      AND workflow.updated_at < (
          EXTRACT(epoch FROM clock_timestamp() - p_retention) * 1000
      )::bigint;

    GET DIAGNOSTICS pruned_count = ROW_COUNT;

    DELETE FROM {{ schema }}.scope AS scope
    WHERE NOT EXISTS (
        SELECT 1
        FROM dbos.workflow_status AS workflow
        WHERE workflow.workflow_uuid = scope.workflow_id
    );

    RAISE NOTICE 'Pruned % DBOS workflows', pruned_count;
END;
$$;
REVOKE ALL ON PROCEDURE {{ schema }}.prune_workflows(interval) FROM public;
