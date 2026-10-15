-- +goose Up
-- noqa: disable=CP02,PRS
-- A workflow is visible to a user when its queue partition names one of the
-- schemas of the token, or when the engine recorded one of those schemas for it.
-- PostgreSQL checks the right to run a function against the caller, not the view
-- owner, so every level can run it.
-- +goose StatementBegin
CREATE FUNCTION admin.sees(workflow text, partition_key text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT admin.mine(partition_key) OR EXISTS (
        SELECT FROM data_proxy.scope AS s
        WHERE s.workflow_id = workflow AND admin.mine(s.schema_name)
    )
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION admin.sees(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.sees(text, text) TO console_viewer;

CREATE OR REPLACE VIEW admin.runs AS
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
LEFT JOIN dbos.workflow_status AS t
    ON
        w.workflow_uuid = t.parent_workflow_id
        AND admin.sees(t.workflow_uuid, t.queue_partition_key)
WHERE w.name = 'run_sync' AND admin.sees(w.workflow_uuid, NULL)
GROUP BY w.workflow_uuid;

CREATE OR REPLACE VIEW admin.tasks AS
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
WHERE
    name IN ('dump_task', 'publish_schema')
    AND admin.sees(workflow_uuid, queue_partition_key);

CREATE OR REPLACE VIEW admin.steps AS
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
WHERE
    w.name IN ('run_sync', 'dump_task', 'publish_schema')
    AND admin.sees(w.workflow_uuid, w.queue_partition_key);

-- One row for each schema of the user: the state of the tables, the last run,
-- and what went wrong in the last day. The dashboard reads it in one request.
CREATE VIEW admin.overview AS
WITH runs AS (
    SELECT
        s.schema_name,
        w.workflow_uuid AS id,
        w.status,
        w.created_at
    FROM data_proxy.scope AS s
    INNER JOIN dbos.workflow_status AS w ON s.workflow_id = w.workflow_uuid
    WHERE w.name = 'run_sync' AND s.table_name = ''
),

latest AS (
    SELECT DISTINCT ON (schema_name)
        schema_name,
        id,
        status,
        created_at
    FROM runs
    ORDER BY schema_name ASC, created_at DESC
),

recent AS (
    SELECT
        schema_name,
        count(*)::int AS runs_24h,
        count(*) FILTER (WHERE status = 'ERROR')::int AS failed_runs_24h
    FROM runs
    WHERE to_timestamp(created_at / 1000.0) > now() - '24 hours'::interval
    GROUP BY schema_name
),

problems AS (
    SELECT
        coalesce(e.fields ->> 'schema', t.schema) AS schema_name,
        count(*)::int AS errors_24h
    FROM data_proxy.errors AS e
    LEFT JOIN admin.tables AS t ON e.fields ->> 'table' = t.name
    WHERE e.recorded_at > now() - '24 hours'::interval
    GROUP BY coalesce(e.fields ->> 'schema', t.schema)
),

counts AS (
    SELECT
        t.schema AS schema_name,
        count(*)::int AS tables,
        count(*) FILTER (WHERE y.synced)::int AS synced_tables
    FROM admin.tables AS t
    INNER JOIN admin.synced AS y ON t.name = y.name
    GROUP BY t.schema
)

SELECT
    s.name,
    l.id AS last_run_id,
    l.status AS last_run_status,
    to_timestamp(l.created_at / 1000.0) AS last_run_at,
    coalesce(c.tables, 0) AS tables,
    coalesce(c.synced_tables, 0) AS synced_tables,
    coalesce(r.runs_24h, 0) AS runs_24h,
    coalesce(r.failed_runs_24h, 0) AS failed_runs_24h,
    coalesce(p.errors_24h, 0) AS errors_24h
FROM admin.schemas AS s
LEFT JOIN latest AS l ON s.name = l.schema_name
LEFT JOIN recent AS r ON s.name = r.schema_name
LEFT JOIN problems AS p ON s.name = p.schema_name
LEFT JOIN counts AS c ON s.name = c.schema_name
WHERE admin.mine(s.name);

-- A schema needs its PostgreSQL schema and its access policy before a run can serve it.
-- The provision job creates both within a minute of the first edit.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION admin.start_sync(
    schemas text[] DEFAULT '{}', tables text[] DEFAULT '{}'
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    scope text[] := admin.start_scope(schemas, tables);
    unknown text[];
    pending text[];
BEGIN
    SELECT array_agg(s.name ORDER BY s.name) INTO unknown
    FROM unnest(scope) AS s (name)
    LEFT JOIN admin.schemas AS c ON s.name = c.name
    WHERE c.name IS NULL;

    IF unknown IS NOT NULL THEN
        RAISE EXCEPTION 'Unknown schemas: %', array_to_string(unknown, ', ')
            USING ERRCODE = 'PT400';
    END IF;

    SELECT array_agg(s.name ORDER BY s.name) INTO pending
    FROM unnest(scope) AS s (name)
    WHERE to_regclass(format('%I.access_policy', s.name)) IS NULL;

    IF pending IS NOT NULL THEN
        RAISE EXCEPTION 'Schemas not provisioned yet: %', array_to_string(pending, ', ')
            USING ERRCODE = 'PT409';
    END IF;

    RETURN dbos.enqueue_workflow(
        workflow_name => 'run_sync',
        queue_name => 'sync',
        positional_args => ARRAY[
            to_json(now()),
            json_build_object('schemas', scope, 'tables', tables)
        ],
        authenticated_user => admin.username()
    );
END
$$;
-- +goose StatementEnd

-- +goose Down
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION admin.start_sync(
    schemas text[] DEFAULT '{}', tables text[] DEFAULT '{}'
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    scope text[] := admin.start_scope(schemas, tables);
    unknown text[];
BEGIN
    SELECT array_agg(s.name ORDER BY s.name) INTO unknown
    FROM unnest(scope) AS s (name)
    LEFT JOIN admin.schemas AS c ON s.name = c.name
    WHERE c.name IS NULL;

    IF unknown IS NOT NULL THEN
        RAISE EXCEPTION 'Unknown schemas: %', array_to_string(unknown, ', ')
            USING ERRCODE = 'PT400';
    END IF;

    RETURN dbos.enqueue_workflow(
        workflow_name => 'run_sync',
        queue_name => 'sync',
        positional_args => ARRAY[
            to_json(now()),
            json_build_object('schemas', scope, 'tables', tables)
        ],
        authenticated_user => admin.username()
    );
END
$$;
-- +goose StatementEnd

DROP VIEW admin.overview;

CREATE OR REPLACE VIEW admin.steps AS
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
WHERE w.name IN ('run_sync', 'dump_task', 'publish_schema') AND admin.is_admin();

CREATE OR REPLACE VIEW admin.tasks AS
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
WHERE name IN ('dump_task', 'publish_schema') AND admin.mine(queue_partition_key);

CREATE OR REPLACE VIEW admin.runs AS
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
WHERE w.name = 'run_sync' AND admin.is_admin()
GROUP BY w.workflow_uuid;

DROP FUNCTION admin.sees(text, text);
