{#
{
  "kind": "template",
  "description": "Create the functions that the console calls through PostgREST: the permissions of the token, the start of a sync run, the control of a run (cancel, resume, run again) and of a schedule (pause, resume, change the cron), and the list of the data files of a table. Every function checks the permission of the token itself, and the migrations of the console and the create_rbac template must have run first.",
  "inputs": {}
}
#}
-- noqa: disable=CP02,PRS
CREATE OR REPLACE FUNCTION console.permissions() RETURNS text[]
LANGUAGE sql STABLE AS $$
    SELECT string_to_array(
        trim(BOTH ',' FROM coalesce(current_setting('rbac.permissions', TRUE), '')), ','
    )
$$;

REVOKE ALL ON FUNCTION console.permissions() FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.start_scope(
    schemas text[] DEFAULT '{}', tables text[] DEFAULT '{}'
) RETURNS text[]
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    scope text[] := schemas;
BEGIN
    IF NOT rbac.can('start_sync') THEN
        RAISE EXCEPTION 'The token lacks the start_sync permission'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF cardinality(scope) = 0 AND cardinality(tables) > 0 THEN
        scope := ARRAY(
            SELECT DISTINCT t.schema FROM console.tables AS t WHERE t.name = ANY (tables)
        );
    ELSIF cardinality(scope) = 0 THEN
        scope := ARRAY(
            SELECT s.name FROM console.schemas AS s
            WHERE rbac.mine(s.name) ORDER BY s.name
        );
    END IF;

    IF cardinality(scope) = 0
        OR EXISTS (SELECT FROM unnest(scope) AS s (name) WHERE NOT rbac.mine(s.name))
        OR EXISTS (
            SELECT FROM unnest(tables) AS n (name)
            LEFT JOIN console.tables AS t ON n.name = t.name
            WHERE t.name IS NULL OR NOT rbac.mine(t.schema)
        )
    THEN
        RAISE EXCEPTION 'The token has no access to what the run asks for'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    RETURN scope;
END
$$;

REVOKE ALL ON FUNCTION console.start_scope(text[], text[]) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.start_sync(
    schemas text[] DEFAULT '{}', tables text[] DEFAULT '{}'
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    scope text[] := console.start_scope(schemas, tables);
    unknown text[];
    pending text[];
    run text;
BEGIN
    SELECT array_agg(s.name ORDER BY s.name) INTO unknown
    FROM unnest(scope) AS s (name)
    LEFT JOIN console.schemas AS c ON s.name = c.name
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

    run := dbos.enqueue_workflow(
        workflow_name => 'run_sync',
        queue_name => 'sync',
        positional_args => ARRAY[
            to_json(now()),
            json_build_object('schemas', scope, 'tables', tables)
        ],
        authenticated_user => console.username()
    );

    INSERT INTO data_proxy.scope (workflow_id, run_id, schema_name)
    SELECT run, run, s.name FROM unnest(scope) AS s (name)
    ON CONFLICT DO NOTHING;

    RETURN run;
END
$$;

REVOKE ALL ON FUNCTION console.start_sync(text[], text[]) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.table_files(schema_name text, table_name text)
RETURNS TABLE (path text, size_bytes bigint, delete_file text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF NOT rbac.can('read_files') OR NOT rbac.mine(schema_name) OR NOT EXISTS (
        SELECT FROM console.tables AS t
        WHERE t.schema = schema_name AND t.name = table_name
    ) THEN
        RAISE EXCEPTION 'The token has no access to this table'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF EXISTS (SELECT FROM console.schemas AS s WHERE s.name = schema_name AND s.encrypted) THEN
        RAISE EXCEPTION 'The files of schema % are encrypted', schema_name
            USING ERRCODE = 'PT409';
    END IF;

    RETURN QUERY EXECUTE format(
        'SELECT f.path, f.size_bytes, f.delete_file FROM %I.dl_table_files($1) AS f',
        schema_name
    ) USING regexp_replace(table_name, '^.*\.', '');
END
$$;

REVOKE ALL ON FUNCTION console.table_files(text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.guard_run(run text, allowed text[])
RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    run_status text;
BEGIN
    IF NOT rbac.can('control_sync') THEN
        RAISE EXCEPTION 'The token lacks the control_sync permission'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT w.status INTO run_status
    FROM dbos.workflow_status AS w
    WHERE
        w.workflow_uuid = run
        AND w.name = 'run_sync'
        AND data_proxy.sees(w.workflow_uuid, NULL);

    IF run_status IS NULL THEN
        RAISE EXCEPTION 'No such run' USING ERRCODE = 'PT404';
    END IF;

    IF NOT run_status = ANY (allowed) THEN
        RAISE EXCEPTION 'The run is %', run_status USING ERRCODE = 'PT409';
    END IF;
END
$$;

REVOKE ALL ON FUNCTION console.guard_run(text, text[]) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.control_run(action text, run text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    allowed text[] := CASE action
        WHEN 'cancel_run' THEN ARRAY['PENDING', 'ENQUEUED', 'DELAYED']
        WHEN 'resume_run' THEN ARRAY['CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED']
    END;
BEGIN
    IF allowed IS NULL THEN
        RAISE EXCEPTION 'Unknown action %', action USING ERRCODE = 'PT400';
    END IF;

    PERFORM console.guard_run(run, allowed);

    PERFORM dbos.enqueue_workflow(
        workflow_name => action,
        queue_name => 'control',
        positional_args => ARRAY[to_json(run)],
        authenticated_user => console.username()
    );
END
$$;

REVOKE ALL ON FUNCTION console.control_run(text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.cancel_run(run text)
RETURNS void
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT console.control_run('cancel_run', run)
$$;

REVOKE ALL ON FUNCTION console.cancel_run(text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.resume_run(run text)
RETURNS void
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT console.control_run('resume_run', run)
$$;

REVOKE ALL ON FUNCTION console.resume_run(text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.retry_run(run text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    scope text[];
BEGIN
    PERFORM console.guard_run(
        run, ARRAY['SUCCESS', 'ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED']
    );

    scope := ARRAY(
        SELECT DISTINCT s.schema_name
        FROM data_proxy.scope AS s
        WHERE s.run_id = run AND rbac.mine(s.schema_name)
        ORDER BY s.schema_name
    );

    IF cardinality(scope) = 0 THEN
        RAISE EXCEPTION 'The run has no schema that the token reaches'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    RETURN console.start_sync(scope, '{}');
END
$$;

REVOKE ALL ON FUNCTION console.retry_run(text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.guard_schedule(target text)
RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    target_schema text := console.schedule_schema(target);
BEGIN
    IF NOT rbac.can('control_sync') THEN
        RAISE EXCEPTION 'The token lacks the control_sync permission'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF target_schema IS NULL
        OR NOT rbac.mine(target_schema)
        OR NOT EXISTS (SELECT FROM console.schemas AS s WHERE s.name = target_schema)
        OR NOT EXISTS (
            SELECT FROM dbos.workflow_schedules AS w WHERE w.schedule_name = target
        )
    THEN
        RAISE EXCEPTION 'No such schedule' USING ERRCODE = 'PT404';
    END IF;
END
$$;

REVOKE ALL ON FUNCTION console.guard_schedule(text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.control_schedule(action text, target text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF action NOT IN ('pause_schedule', 'resume_schedule') THEN
        RAISE EXCEPTION 'Unknown action %', action USING ERRCODE = 'PT400';
    END IF;

    PERFORM console.guard_schedule(target);

    PERFORM dbos.enqueue_workflow(
        workflow_name => action,
        queue_name => 'control',
        positional_args => ARRAY[to_json(target)],
        authenticated_user => console.username()
    );
END
$$;

REVOKE ALL ON FUNCTION console.control_schedule(text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.pause_schedule(schedule_name text)
RETURNS void
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT console.control_schedule('pause_schedule', schedule_name)
$$;

REVOKE ALL ON FUNCTION console.pause_schedule(text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.resume_schedule(schedule_name text)
RETURNS void
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT console.control_schedule('resume_schedule', schedule_name)
$$;

REVOKE ALL ON FUNCTION console.resume_schedule(text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.set_schedule(schedule_name text, cron text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    PERFORM console.guard_schedule(schedule_name);

    IF cron !~ '^[0-9A-Za-z*/,?#-]+( [0-9A-Za-z*/,?#-]+){4,5}$' THEN
        RAISE EXCEPTION 'The cron needs 5 or 6 fields, separated by spaces'
            USING ERRCODE = 'PT400';
    END IF;

    PERFORM dbos.enqueue_workflow(
        workflow_name => 'set_schedule',
        queue_name => 'control',
        positional_args => ARRAY[to_json(schedule_name), to_json(cron)],
        authenticated_user => console.username()
    );
END
$$;

REVOKE ALL ON FUNCTION console.set_schedule(text, text) FROM PUBLIC;
