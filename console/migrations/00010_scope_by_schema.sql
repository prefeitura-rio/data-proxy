-- +goose Up
-- noqa: disable=CP02,PRS
-- The role setting holds the role that PostgREST switched to, also inside a
-- SECURITY DEFINER function, where current_user is the owner.
-- The admin sees every schema. Any other level sees the schemas of its token.
-- +goose StatementBegin
CREATE FUNCTION admin.is_admin() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT pg_has_role(
        coalesce(nullif(current_setting('role'), 'none'), session_user),
        'console_admin',
        'member'
    )
$$;

CREATE FUNCTION admin.mine(schema_name text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT admin.is_admin() OR coalesce(
        schema_name = ANY (
            string_to_array(
                coalesce(current_setting('app.claim_schemas', TRUE), ''), ','
            )
        ),
        FALSE
    )
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION admin.is_admin() FROM PUBLIC;
REVOKE ALL ON FUNCTION admin.mine(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.is_admin() TO console_viewer;
GRANT EXECUTE ON FUNCTION admin.mine(text) TO console_viewer;

-- The integrity checks read every row, so a rule for a table of another schema
-- fails the policy, not the trigger.
ALTER FUNCTION admin.require_claim()
SECURITY DEFINER SET search_path = pg_catalog, pg_temp;

ALTER TABLE admin.schemas ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin.tables ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin.rls ENABLE ROW LEVEL SECURITY;

CREATE POLICY scope ON admin.schemas
USING (admin.mine(name)) WITH CHECK (admin.mine(name));

CREATE POLICY scope ON admin.tables
USING (admin.mine(schema)) WITH CHECK (admin.mine(schema));

CREATE POLICY scope ON admin.rls
USING (
    EXISTS (
        SELECT FROM admin.tables AS t
        WHERE t.name = rls.table_name AND admin.mine(t.schema)
    )
)
WITH CHECK (
    EXISTS (
        SELECT FROM admin.tables AS t
        WHERE t.name = rls.table_name AND admin.mine(t.schema)
    )
);

-- The views run as their owner, so they filter by themselves.
-- A run and its steps don't name a schema yet, so only the admin reads them.
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

CREATE OR REPLACE VIEW admin.logs AS
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
WHERE s.key = 'logs' AND s.serialization = 'portable_json' AND admin.mine(e.schema);

CREATE OR REPLACE VIEW admin.errors AS
SELECT
    e.id,
    e.reason,
    e.fields,
    e.recorded_at
FROM data_proxy.errors AS e
LEFT JOIN admin.tables AS t ON e.fields ->> 'table' = t.name
WHERE admin.mine(coalesce(e.fields ->> 'schema', t.schema))
ORDER BY e.recorded_at DESC;

CREATE OR REPLACE VIEW admin.health AS
WITH objects AS (
    SELECT
        n.nspname AS name,
        bool_or(c.relname = 'access_policy') AS access_policy,
        count(c.oid) FILTER (WHERE c.relkind = 'v')::int AS view_count
    FROM pg_catalog.pg_namespace AS n
    LEFT JOIN pg_catalog.pg_class AS c ON n.oid = c.relnamespace
    GROUP BY n.nspname
)

SELECT
    s.name,
    o.name IS NOT NULL AS schema_exists,
    coalesce(o.access_policy, FALSE) AS access_policy,
    r.rolname IS NOT NULL AS policy_writer,
    coalesce(o.view_count, 0) AS view_count
FROM admin.schemas AS s
LEFT JOIN objects AS o ON s.name = o.name
LEFT JOIN pg_catalog.pg_roles AS r ON 'policy_writer_' || s.name = r.rolname
WHERE admin.mine(s.name);

CREATE OR REPLACE VIEW admin.synced AS
SELECT
    t.schema,
    t.name,
    st.table_name IS NOT NULL AS synced,
    st.state ->> 'signature' AS signature,
    coalesce(
        jsonb_array_length(
            jsonb_path_query_array(st.state, '$.partitions.keyvalue().key')
        ),
        0
    ) AS partition_count
FROM admin.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name
WHERE admin.mine(t.schema);

-- Queues and schedules serve every schema, so only the admin changes them.
REVOKE UPDATE ON admin.queues FROM console_operator;
REVOKE UPDATE ON admin.schedules FROM console_operator;

GRANT UPDATE (
    concurrency,
    worker_concurrency,
    rate_limit_max,
    rate_limit_period_sec,
    polling_interval_sec
) ON admin.queues TO console_admin;

GRANT UPDATE (schedule, status, timezone, automatic_backfill)
ON admin.schedules TO console_admin;

-- An operator starts runs for the schemas of its token. With no arguments it
-- starts every schema of the token. A schema or a table outside the token is
-- refused, and so is a table that doesn't exist, so a name tells nothing.
-- The admin starts what it asks for, and an empty list of schemas means every schema.
-- +goose StatementBegin
CREATE FUNCTION admin.start_scope(
    schemas text[] DEFAULT '{}', tables text[] DEFAULT '{}'
) RETURNS text[]
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    scope text[] := schemas;
BEGIN
    IF admin.is_admin() THEN
        RETURN scope;
    END IF;

    IF cardinality(scope) = 0 AND cardinality(tables) > 0 THEN
        scope := ARRAY(
            SELECT DISTINCT t.schema FROM admin.tables AS t WHERE t.name = ANY (tables)
        );
    ELSIF cardinality(scope) = 0 THEN
        scope := ARRAY(
            SELECT s.name FROM admin.schemas AS s
            WHERE admin.mine(s.name) ORDER BY s.name
        );
    END IF;

    IF cardinality(scope) = 0
        OR EXISTS (SELECT FROM unnest(scope) AS s (name) WHERE NOT admin.mine(s.name))
        OR EXISTS (
            SELECT FROM unnest(tables) AS n (name)
            LEFT JOIN admin.tables AS t ON n.name = t.name
            WHERE t.name IS NULL OR NOT admin.mine(t.schema)
        )
    THEN
        RAISE EXCEPTION 'The token has no access to what the run asks for'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    RETURN scope;
END
$$;

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

REVOKE ALL ON FUNCTION admin.start_scope(text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.start_scope(text[], text[]) TO console_operator;

-- +goose Down
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION admin.start_sync(
    schemas text[] DEFAULT '{}', tables text[] DEFAULT '{}'
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    unknown text[];
BEGIN
    SELECT array_agg(s.name ORDER BY s.name) INTO unknown
    FROM unnest(schemas) AS s (name)
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
            json_build_object('schemas', schemas, 'tables', tables)
        ],
        authenticated_user => admin.username()
    );
END
$$;
-- +goose StatementEnd

DROP FUNCTION admin.start_scope(text[], text[]);

GRANT UPDATE (schedule, status, timezone, automatic_backfill)
ON admin.schedules TO console_operator;
GRANT UPDATE (
    concurrency,
    worker_concurrency,
    rate_limit_max,
    rate_limit_period_sec,
    polling_interval_sec
) ON admin.queues TO console_operator;
REVOKE UPDATE ON admin.schedules FROM console_admin;
REVOKE UPDATE ON admin.queues FROM console_admin;

ALTER FUNCTION admin.require_claim() SECURITY INVOKER RESET search_path;

DROP POLICY scope ON admin.rls;
DROP POLICY scope ON admin.tables;
DROP POLICY scope ON admin.schemas;
ALTER TABLE admin.rls DISABLE ROW LEVEL SECURITY;
ALTER TABLE admin.tables DISABLE ROW LEVEL SECURITY;
ALTER TABLE admin.schemas DISABLE ROW LEVEL SECURITY;

CREATE OR REPLACE VIEW admin.synced AS
SELECT
    t.schema,
    t.name,
    st.table_name IS NOT NULL AS synced,
    st.state ->> 'signature' AS signature,
    coalesce(
        jsonb_array_length(
            jsonb_path_query_array(st.state, '$.partitions.keyvalue().key')
        ),
        0
    ) AS partition_count
FROM admin.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name;

CREATE OR REPLACE VIEW admin.health AS
WITH objects AS (
    SELECT
        n.nspname AS name,
        bool_or(c.relname = 'access_policy') AS access_policy,
        count(c.oid) FILTER (WHERE c.relkind = 'v')::int AS view_count
    FROM pg_catalog.pg_namespace AS n
    LEFT JOIN pg_catalog.pg_class AS c ON n.oid = c.relnamespace
    GROUP BY n.nspname
)

SELECT
    s.name,
    o.name IS NOT NULL AS schema_exists,
    coalesce(o.access_policy, FALSE) AS access_policy,
    r.rolname IS NOT NULL AS policy_writer,
    coalesce(o.view_count, 0) AS view_count
FROM admin.schemas AS s
LEFT JOIN objects AS o ON s.name = o.name
LEFT JOIN pg_catalog.pg_roles AS r ON 'policy_writer_' || s.name = r.rolname;

CREATE OR REPLACE VIEW admin.errors AS
SELECT
    id,
    reason,
    fields,
    recorded_at
FROM data_proxy.errors
ORDER BY recorded_at DESC;

CREATE OR REPLACE VIEW admin.logs AS
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
WHERE w.name IN ('run_sync', 'dump_task', 'publish_schema');

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
WHERE name IN ('dump_task', 'publish_schema');

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
WHERE w.name = 'run_sync'
GROUP BY w.workflow_uuid;

DROP FUNCTION admin.mine(text);
DROP FUNCTION admin.is_admin();
