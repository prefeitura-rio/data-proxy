-- +goose Up
-- Redefines console.synced, widens the schema name check, adds the run schemas to
-- console.runs, creates console.config_history with record_change triggers, and adds
-- console.schedule_schema() and a scoped console.schedules view. Squashed from the old
-- console migrations 00006 to 00010.
-- noqa: disable=PRS

-- From console migration 00006_fix_synced_partitions
CREATE OR REPLACE VIEW console.synced AS
SELECT
    t.schema,
    t.name,
    st.table_name IS NOT NULL AS synced,
    st.state ->> 'signature' AS signature,
    CASE
        WHEN jsonb_typeof(st.state -> 'partitions') = 'object'
            THEN (SELECT count(*) FROM jsonb_object_keys(st.state -> 'partitions'))::int
        ELSE 0
    END AS partition_count
FROM console.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name
WHERE rbac.can('read_sync') AND rbac.mine(t.schema);

-- From console migration 00007_limit_schema_name
-- noqa: disable=CP02,PRS
-- A schema gets the database role schema_ and its name, at most 63 characters.
ALTER TABLE console.schemas DROP CONSTRAINT schemas_name_check;
ALTER TABLE console.schemas ADD CONSTRAINT schemas_name_check
CHECK (name ~ '^[a-z][a-z0-9_]{0,55}$');

-- From console migration 00008_add_run_schemas
-- noqa: disable=CP02,PRS
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

-- From console migration 00009_create_config_history
-- noqa: disable=CP02,PRS
CREATE TABLE console.config_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_at timestamptz NOT NULL DEFAULT now(),
    changed_by text,
    action text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    object text NOT NULL CHECK (object IN ('schemas', 'tables', 'rls')),
    schema_name text NOT NULL,
    key text NOT NULL,
    old_row jsonb,
    new_row jsonb
);

CREATE INDEX config_history_by_time
ON console.config_history (changed_at DESC, id DESC);

ALTER TABLE console.config_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY read_scope ON console.config_history FOR SELECT
USING (rbac.can('read_config') AND rbac.mine(schema_name));

-- +goose StatementBegin
CREATE FUNCTION console.record_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    old_json jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    new_json jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
    changed jsonb := coalesce(new_json, old_json);
    in_schema text;
BEGIN
    in_schema := CASE TG_TABLE_NAME
        WHEN 'schemas' THEN changed ->> 'name'
        WHEN 'tables' THEN changed ->> 'schema'
        ELSE (
            SELECT t.schema FROM console.tables AS t WHERE t.name = changed ->> 'table_name'
        )
    END;

    IF in_schema IS NULL THEN
        RETURN NULL;
    END IF;

    INSERT INTO console.config_history (
        changed_by, action, object, schema_name, key, old_row, new_row
    )
    VALUES (
        console.username(),
        lower(TG_OP),
        TG_TABLE_NAME,
        in_schema,
        coalesce(changed ->> 'name', (changed ->> 'table_name') || '.' || (changed ->> 'column_name')),
        old_json,
        new_json
    );

    RETURN NULL;
END
$$;
-- +goose StatementEnd

CREATE TRIGGER record_change
AFTER INSERT OR UPDATE OR DELETE ON console.schemas
FOR EACH ROW EXECUTE FUNCTION console.record_change();
CREATE TRIGGER record_change
AFTER INSERT OR UPDATE OR DELETE ON console.tables
FOR EACH ROW EXECUTE FUNCTION console.record_change();
CREATE TRIGGER record_change
AFTER INSERT OR UPDATE OR DELETE ON console.rls
FOR EACH ROW EXECUTE FUNCTION console.record_change();

-- From console migration 00010_scope_schedules_by_schema
-- noqa: disable=CP02,PRS
-- The one place in SQL that knows the prefix of the schedule of a schema.
-- +goose StatementBegin
CREATE FUNCTION console.schedule_schema(schedule_name text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN starts_with(schedule_name, 'sync-') AND length(schedule_name) > 5
            THEN substr(schedule_name, 6)
    END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.schedule_schema(text) FROM PUBLIC;

CREATE OR REPLACE VIEW console.schedules AS
SELECT
    schedule_name AS name,
    workflow_name,
    schedule,
    status,
    queue_name,
    cron_timezone AS timezone,
    automatic_backfill,
    last_fired_at,
    console.schedule_schema(schedule_name) AS schema
FROM dbos.workflow_schedules
WHERE
    console.schedule_schema(schedule_name) IS NOT NULL
    AND rbac.can('read_sync')
    AND rbac.mine(console.schedule_schema(schedule_name));
