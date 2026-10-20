{#
{
  "kind": "template",
  "description": "Create the functions that the console calls through PostgREST: the permissions of the token, the start of a sync run, and the list of the data files of a table. Every function checks the permission of the token itself, and the migrations of the console and the create_rbac template must have run first.",
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
