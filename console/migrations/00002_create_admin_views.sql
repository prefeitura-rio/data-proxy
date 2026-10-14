-- +goose Up
CREATE SCHEMA admin;

CREATE VIEW admin.runs AS
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
    count(t.workflow_uuid) FILTER (WHERE t.status = 'ERROR')::int AS failed_task_count
FROM dbos.workflow_status AS w
LEFT JOIN dbos.workflow_status AS t ON w.workflow_uuid = t.parent_workflow_id
WHERE w.name = 'run_sync'
GROUP BY w.workflow_uuid;

CREATE VIEW admin.tasks AS
SELECT
    workflow_uuid AS id,
    parent_workflow_id AS run_id,
    name,
    status,
    queue_name,
    queue_partition_key AS schema_name,
    to_timestamp(created_at / 1000.0) AS created_at,
    to_timestamp(started_at_epoch_ms / 1000.0) AS started_at,
    to_timestamp(completed_at / 1000.0) AS completed_at
FROM dbos.workflow_status
WHERE name IN ('dump_task', 'publish_schema');

CREATE VIEW admin.steps AS
SELECT
    o.workflow_uuid AS workflow_id,
    o.function_id,
    o.function_name,
    o.child_workflow_id,
    coalesce(w.parent_workflow_id, w.workflow_uuid) AS run_id,
    o.error IS NOT NULL AS failed,
    to_timestamp(o.started_at_epoch_ms / 1000.0) AS started_at,
    to_timestamp(o.completed_at_epoch_ms / 1000.0) AS completed_at
FROM dbos.operation_outputs AS o
INNER JOIN dbos.workflow_status AS w ON o.workflow_uuid = w.workflow_uuid
WHERE w.name IN ('run_sync', 'dump_task', 'publish_schema');

CREATE VIEW admin.logs AS
SELECT
    s.workflow_uuid AS workflow_id,
    s."offset" AS position,
    e.time AS logged_at,
    e.level,
    e.message,
    e.fields,
    e.schema AS schema_name,
    e.table AS table_name,
    coalesce(w.parent_workflow_id, w.workflow_uuid) AS run_id
FROM dbos.streams AS s
INNER JOIN dbos.workflow_status AS w ON s.workflow_uuid = w.workflow_uuid
CROSS JOIN LATERAL jsonb_to_record(s.value::jsonb) AS e (
    time timestamptz,
    level text,
    message text,
    fields jsonb,
    schema text,
    "table" text
)
WHERE s.key = 'logs' AND s.serialization = 'portable_json';

CREATE VIEW admin.queues AS
SELECT
    q.name,
    q.concurrency,
    q.worker_concurrency,
    q.rate_limit_max,
    q.rate_limit_period_sec,
    q.partition_queue,
    q.partition_concurrency,
    q.polling_interval_sec,
    to_timestamp(q.updated_at / 1000.0) AS updated_at,
    count(w.workflow_uuid) FILTER (WHERE w.status = 'ENQUEUED')::int AS enqueued,
    count(w.workflow_uuid) FILTER (WHERE w.status = 'PENDING')::int AS pending,
    count(w.workflow_uuid) FILTER (WHERE w.status = 'DELAYED')::int AS delayed
FROM dbos.queues AS q
LEFT JOIN dbos.workflow_status AS w
    ON q.name = w.queue_name AND w.status IN ('ENQUEUED', 'PENDING', 'DELAYED')
GROUP BY q.queue_id;

CREATE VIEW admin.schedules AS
SELECT
    schedule_name AS name,
    workflow_name,
    schedule,
    status,
    queue_name,
    cron_timezone AS timezone,
    automatic_backfill,
    last_fired_at
FROM dbos.workflow_schedules;

CREATE VIEW admin.errors AS
SELECT
    id,
    reason,
    fields,
    recorded_at
FROM data_proxy.errors
ORDER BY recorded_at DESC;

-- +goose Down
DROP SCHEMA admin CASCADE;
