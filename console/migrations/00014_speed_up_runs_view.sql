-- +goose Up
-- noqa: disable=CP02,PRS
-- The view grouped every run with all its tasks before a page took its first rows.
-- The counts are subqueries now, so a page counts the tasks of its own runs only.
-- A count of all runs drops the subqueries, because it reads none of their
-- columns. The list of schemas names each schema once. It named a schema once for
-- each task of the run.
CREATE OR REPLACE VIEW console.runs AS
SELECT
    w.workflow_uuid AS id,
    w.status,
    w.queue_name,
    w.schedule_name,
    w.authenticated_user AS started_by,
    w.executor_id,
    w.application_version,
    w.recovery_attempts,
    to_timestamp(w.created_at / 1000.0) AS created_at,
    to_timestamp(w.started_at_epoch_ms / 1000.0) AS started_at,
    to_timestamp(w.completed_at / 1000.0) AS completed_at,
    (
        SELECT count(*)::int
        FROM dbos.workflow_status AS t
        WHERE
            t.parent_workflow_id = w.workflow_uuid
            AND data_proxy.sees(t.workflow_uuid, t.queue_partition_key)
    ) AS task_count,
    (
        SELECT count(*)::int
        FROM dbos.workflow_status AS t
        WHERE
            t.parent_workflow_id = w.workflow_uuid
            AND t.status = 'ERROR'
            AND data_proxy.sees(t.workflow_uuid, t.queue_partition_key)
    ) AS failed_task_count,
    (
        SELECT coalesce(array_agg(DISTINCT s.schema_name), '{}')
        FROM data_proxy.scope AS s
        WHERE s.run_id = w.workflow_uuid AND rbac.mine(s.schema_name)
    ) AS schemas
FROM dbos.workflow_status AS w
WHERE
    w.name = 'run_sync'
    AND rbac.can('read_sync')
    AND data_proxy.sees(w.workflow_uuid, NULL);

-- +goose Down
CREATE OR REPLACE VIEW console.runs AS
SELECT
    w.workflow_uuid AS id,
    w.status,
    w.queue_name,
    w.schedule_name,
    w.authenticated_user AS started_by,
    w.executor_id,
    w.application_version,
    w.recovery_attempts,
    to_timestamp(w.created_at / 1000.0) AS created_at,
    to_timestamp(w.started_at_epoch_ms / 1000.0) AS started_at,
    to_timestamp(w.completed_at / 1000.0) AS completed_at,
    count(t.workflow_uuid)::int AS task_count,
    count(t.workflow_uuid) FILTER (WHERE t.status = 'ERROR')::int AS failed_task_count,
    (
        SELECT coalesce(array_agg(s.schema_name ORDER BY s.schema_name), '{}')
        FROM data_proxy.scope AS s
        WHERE s.run_id = w.workflow_uuid AND rbac.mine(s.schema_name)
    ) AS schemas
FROM dbos.workflow_status AS w
LEFT JOIN dbos.workflow_status AS t
    ON
        w.workflow_uuid = t.parent_workflow_id
        AND data_proxy.sees(t.workflow_uuid, t.queue_partition_key)
WHERE
    w.name = 'run_sync'
    AND rbac.can('read_sync')
    AND data_proxy.sees(w.workflow_uuid, NULL)
GROUP BY w.workflow_uuid;
