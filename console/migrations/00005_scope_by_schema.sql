-- +goose Up
-- noqa: disable=CP02,PRS
ALTER FUNCTION console.require_claim()
SECURITY DEFINER SET search_path = pg_catalog, pg_temp;

ALTER TABLE console.schemas ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.tables ENABLE ROW LEVEL SECURITY;
ALTER TABLE console.rls ENABLE ROW LEVEL SECURITY;

CREATE POLICY read_scope ON console.schemas FOR SELECT
USING (rbac.can('read_config') AND rbac.mine(name));
CREATE POLICY insert_scope ON console.schemas FOR INSERT
WITH CHECK (rbac.can('create_config') AND rbac.mine(name));
CREATE POLICY update_scope ON console.schemas FOR UPDATE
USING (rbac.can('edit_config') AND rbac.mine(name))
WITH CHECK (rbac.can('edit_config') AND rbac.mine(name));
CREATE POLICY delete_scope ON console.schemas FOR DELETE
USING (rbac.can('delete_config') AND rbac.mine(name));

CREATE POLICY read_scope ON console.tables FOR SELECT
USING (rbac.can('read_config') AND rbac.mine(schema));
CREATE POLICY insert_scope ON console.tables FOR INSERT
WITH CHECK (rbac.can('create_config') AND rbac.mine(schema));
CREATE POLICY update_scope ON console.tables FOR UPDATE
USING (rbac.can('edit_config') AND rbac.mine(schema))
WITH CHECK (rbac.can('edit_config') AND rbac.mine(schema));
CREATE POLICY delete_scope ON console.tables FOR DELETE
USING (rbac.can('delete_config') AND rbac.mine(schema));

-- +goose StatementBegin
CREATE FUNCTION console.mine_table(table_name text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT FROM console.tables AS t
        WHERE t.name = table_name AND rbac.mine(t.schema)
    )
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.mine_table(text) FROM PUBLIC;

CREATE POLICY read_scope ON console.rls FOR SELECT
USING (rbac.can('read_config') AND console.mine_table(table_name));
CREATE POLICY insert_scope ON console.rls FOR INSERT
WITH CHECK (rbac.can('create_config') AND console.mine_table(table_name));
CREATE POLICY update_scope ON console.rls FOR UPDATE
USING (rbac.can('edit_config') AND console.mine_table(table_name))
WITH CHECK (rbac.can('edit_config') AND console.mine_table(table_name));
CREATE POLICY delete_scope ON console.rls FOR DELETE
USING (rbac.can('delete_config') AND console.mine_table(table_name));

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
    count(t.workflow_uuid) FILTER (WHERE t.status = 'ERROR')::int AS failed_task_count
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


CREATE OR REPLACE VIEW console.tasks AS
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
    AND rbac.can('read_sync')
    AND data_proxy.sees(workflow_uuid, queue_partition_key);


CREATE OR REPLACE VIEW console.steps AS
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
    AND rbac.can('read_sync')
    AND data_proxy.sees(w.workflow_uuid, w.queue_partition_key);


CREATE OR REPLACE VIEW console.logs AS
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
WHERE
    s.key = 'logs'
    AND s.serialization = 'portable_json'
    AND rbac.can('read_sync')
    AND rbac.mine(e.schema);


CREATE OR REPLACE VIEW console.errors AS
SELECT
    e.id,
    e.reason,
    e.fields,
    e.recorded_at
FROM data_proxy.errors AS e
LEFT JOIN console.tables AS t ON e.fields ->> 'table' = t.name
WHERE
    rbac.can('read_sync')
    AND rbac.mine(coalesce(e.fields ->> 'schema', t.schema))
ORDER BY e.recorded_at DESC;


CREATE OR REPLACE VIEW console.health AS
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
    coalesce(o.view_count, 0) AS view_count
FROM console.schemas AS s
LEFT JOIN objects AS o ON s.name = o.name
WHERE rbac.can('read_sync') AND rbac.mine(s.name);


CREATE OR REPLACE VIEW console.synced AS
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
FROM console.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name
WHERE rbac.mine(t.schema);


CREATE VIEW console.overview AS
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
    LEFT JOIN console.tables AS t ON e.fields ->> 'table' = t.name
    WHERE e.recorded_at > now() - '24 hours'::interval
    GROUP BY coalesce(e.fields ->> 'schema', t.schema)
),

counts AS (
    SELECT
        t.schema AS schema_name,
        count(*)::int AS tables,
        count(*) FILTER (WHERE y.synced)::int AS synced_tables
    FROM console.tables AS t
    INNER JOIN console.synced AS y ON t.name = y.name
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
FROM console.schemas AS s
LEFT JOIN latest AS l ON s.name = l.schema_name
LEFT JOIN recent AS r ON s.name = r.schema_name
LEFT JOIN problems AS p ON s.name = p.schema_name
LEFT JOIN counts AS c ON s.name = c.schema_name
WHERE rbac.can('read_sync') AND rbac.mine(s.name);


-- +goose Down
DROP VIEW console.overview;

CREATE OR REPLACE VIEW console.synced AS
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
FROM console.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name;


CREATE OR REPLACE VIEW console.health AS
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
    coalesce(o.view_count, 0) AS view_count
FROM console.schemas AS s
LEFT JOIN objects AS o ON s.name = o.name;


CREATE OR REPLACE VIEW console.errors AS
SELECT
    id,
    reason,
    fields,
    recorded_at
FROM data_proxy.errors
ORDER BY recorded_at DESC;


CREATE OR REPLACE VIEW console.logs AS
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


CREATE OR REPLACE VIEW console.steps AS
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


CREATE OR REPLACE VIEW console.tasks AS
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
    count(t.workflow_uuid) FILTER (WHERE t.status = 'ERROR')::int AS failed_task_count
FROM dbos.workflow_status AS w
LEFT JOIN dbos.workflow_status AS t ON w.workflow_uuid = t.parent_workflow_id
WHERE w.name = 'run_sync'
GROUP BY w.workflow_uuid;


DROP POLICY delete_scope ON console.rls;
DROP POLICY update_scope ON console.rls;
DROP POLICY insert_scope ON console.rls;
DROP POLICY read_scope ON console.rls;
DROP POLICY delete_scope ON console.tables;
DROP POLICY update_scope ON console.tables;
DROP POLICY insert_scope ON console.tables;
DROP POLICY read_scope ON console.tables;
DROP POLICY delete_scope ON console.schemas;
DROP POLICY update_scope ON console.schemas;
DROP POLICY insert_scope ON console.schemas;
DROP POLICY read_scope ON console.schemas;
DROP FUNCTION console.mine_table(text);

ALTER TABLE console.rls DISABLE ROW LEVEL SECURITY;
ALTER TABLE console.tables DISABLE ROW LEVEL SECURITY;
ALTER TABLE console.schemas DISABLE ROW LEVEL SECURITY;
ALTER FUNCTION console.require_claim() SECURITY INVOKER RESET search_path;
