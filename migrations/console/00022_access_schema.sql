-- +goose Up
-- noqa: disable=PRS,CP02
-- Creates the schema access with access.policy and access.log, one table of each for
-- every project. A policy row has a scope of one ltree: the empty path for every
-- project, `project`, `project.schema` or `project.schema.table`. The rows of the
-- policy table of each schema move here, and a row without a unit is dropped.
-- The old tables stay until a later release drops them. A policy needs the
-- permission of its action and a scope in the reach of the token. The log has
-- no update and no delete, and access.prune_log() removes old rows. A schema counts
-- as provisioned when it exists in the database.

CREATE SCHEMA IF NOT EXISTS access;

CREATE TABLE access.policy (
    subject text NOT NULL CHECK (subject <> ''),
    scope ltree NOT NULL CHECK (nlevel(scope) <= 3),
    unit_type text NOT NULL CHECK (unit_type <> ''),
    unit_id text NOT NULL CHECK (unit_id <> ''),
    UNIQUE (subject, scope, unit_type, unit_id)
);

CREATE INDEX policy_scope ON access.policy USING gist (scope);

CREATE TABLE access.log (
    subject text NOT NULL,
    scope ltree NOT NULL,
    unit_type text NOT NULL,
    unit_id text NOT NULL,
    action text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX log_changed_at ON access.log (changed_at);

COMMENT ON TABLE access.policy IS
'The grants of every project: the units that a subject may read, in a scope.';
COMMENT ON TABLE access.log IS
'The audit trail of the grants. A trigger writes it, and only prune_log removes rows.';

-- +goose StatementBegin
DO $copy$
DECLARE
    v_schema record;
BEGIN
    FOR v_schema IN
        SELECT s.name, s.project, s.short_name
        FROM console.schemas AS s
        WHERE to_regclass(format('%I.access_policy', s.name)) IS NOT NULL
    LOOP
        EXECUTE format(
            'INSERT INTO access.policy (subject, scope, unit_type, unit_id) '
            || 'SELECT subject, text2ltree(%L), unit_type, unit_id FROM %I.access_policy '
            || 'WHERE subject IS NOT NULL AND unit_type IS NOT NULL AND unit_id IS NOT NULL '
            || 'ON CONFLICT DO NOTHING',
            v_schema.project || '.' || v_schema.short_name, v_schema.name
        );
    END LOOP;
END
$copy$;
-- +goose StatementEnd

-- Whether a scope covers a path: the scope is the path or one of its ancestors.
-- A function of the data schemas calls it, and their search path doesn't hold the
-- schema of the extension.
-- +goose StatementBegin
CREATE FUNCTION access.covers(p_scope ltree, p_path text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog, public AS $$
    SELECT p_scope @> text2ltree(p_path)
$$;
-- +goose StatementEnd

-- Whether the token reaches a scope: grant_all for the empty path, the project for a
-- path of one label, and the schema or its project for a longer path.
-- +goose StatementBegin
CREATE FUNCTION access.in_reach(p_scope ltree) RETURNS boolean
LANGUAGE sql STABLE
SET search_path = pg_catalog, public AS $$
    SELECT CASE nlevel(p_scope)
        WHEN 0 THEN rbac.can('grant_all')
        WHEN 1 THEN rbac.mine(NULL, p_scope::text)
        ELSE rbac.mine(
            subpath(p_scope, 0, 1)::text || '__' || subpath(p_scope, 1, 1)::text,
            subpath(p_scope, 0, 1)::text
        )
    END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION access.log_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_changed access.policy := CASE WHEN TG_OP = 'INSERT' THEN new ELSE old END;
BEGIN
    INSERT INTO access.log (subject, scope, unit_type, unit_id, action)
    VALUES (v_changed.subject, v_changed.scope, v_changed.unit_type, v_changed.unit_id, lower(TG_OP));
    RETURN NULL;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION access.require_remote_apply() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM set_config('synchronous_commit', 'remote_apply', TRUE);
    RETURN NULL;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION access.bump_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    UPDATE rbac.revision SET value = value + 1;
    RETURN NULL;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION access.keep_log() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('access.pruning', TRUE), '') <> 'on' THEN
        RAISE EXCEPTION 'The access log only grows' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN old;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE PROCEDURE access.prune_log(p_retention interval)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    PERFORM set_config('access.pruning', 'on', TRUE);
    DELETE FROM access.log WHERE changed_at < now() - p_retention;
END
$$;
-- +goose StatementEnd

CREATE TRIGGER log_change AFTER INSERT OR UPDATE OR DELETE ON access.policy
FOR EACH ROW EXECUTE FUNCTION access.log_change();
CREATE TRIGGER commit_remote_apply AFTER INSERT OR UPDATE OR DELETE ON access.policy
FOR EACH STATEMENT EXECUTE FUNCTION access.require_remote_apply();
CREATE TRIGGER bump_revision AFTER INSERT OR UPDATE OR DELETE ON access.policy
FOR EACH STATEMENT EXECUTE FUNCTION access.bump_revision();
CREATE TRIGGER keep_log BEFORE UPDATE OR DELETE ON access.log
FOR EACH ROW EXECUTE FUNCTION access.keep_log();

ALTER TABLE access.policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE access.log ENABLE ROW LEVEL SECURITY;

CREATE POLICY read_scope ON access.policy FOR SELECT
USING (rbac.can('read_policy') AND access.in_reach(scope));
CREATE POLICY insert_scope ON access.policy FOR INSERT
WITH CHECK (rbac.can('create_policy') AND access.in_reach(scope));
CREATE POLICY update_scope ON access.policy FOR UPDATE
USING (rbac.can('edit_policy') AND access.in_reach(scope))
WITH CHECK (rbac.can('edit_policy') AND access.in_reach(scope));
CREATE POLICY delete_scope ON access.policy FOR DELETE
USING (rbac.can('delete_policy') AND access.in_reach(scope));
CREATE POLICY read_scope ON access.log FOR SELECT
USING (rbac.can('read_policy') AND access.in_reach(scope));

REVOKE ALL ON FUNCTION
access.covers(ltree, text), access.in_reach(ltree), access.log_change(),
access.require_remote_apply(), access.bump_revision(), access.keep_log()
FROM PUBLIC;
REVOKE ALL ON PROCEDURE access.prune_log(interval) FROM PUBLIC;

-- noqa: disable=RF05
-- +goose ENVSUB ON
GRANT USAGE ON SCHEMA access TO "${AUTH_USER_ROLE}";
GRANT SELECT, INSERT, UPDATE, DELETE ON access.policy TO "${AUTH_USER_ROLE}";
GRANT SELECT ON access.log TO "${AUTH_USER_ROLE}";
GRANT EXECUTE ON FUNCTION access.in_reach(ltree) TO "${AUTH_USER_ROLE}";
GRANT EXECUTE ON FUNCTION access.covers(ltree, text) TO "${AUTH_USER_ROLE}";
-- +goose ENVSUB OFF

-- The old procedure deleted the log rows of one schema.
DROP PROCEDURE data_proxy.prune_access_log(interval, text);

-- The statement timeout of the console profile covers the profile access too.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION data_proxy.pre_request() RETURNS void AS $$
DECLARE
    claims jsonb := coalesce(
        current_setting('request.jwt.claims', true)::jsonb,
        '{}'::jsonb
    );
    claim_name text;
    claim_value jsonb;
BEGIN
    FOR claim_name, claim_value IN
        SELECT * FROM jsonb_each(claims)
        -- A claim name that is not a plain word, such as allowed-origins from Keycloak, can't be a setting name.
        WHERE key ~ '^[A-Za-z_][A-Za-z0-9_]*$'
    LOOP
        PERFORM set_config(
            'app.claim_' || claim_name,
            CASE jsonb_typeof(claim_value)
                WHEN 'array' THEN (
                    SELECT string_agg(value, ',')
                    FROM jsonb_array_elements_text(claim_value)
                )
                ELSE trim(both '"' FROM claim_value::text)
            END,
            true
        );
    END LOOP;

    PERFORM rbac.load();

    -- The console reads views over the DBOS tables, so its requests may run longer than data requests.
    -- The role of the token has the short timeout, and every token has the same role.
    IF coalesce(
        nullif(current_setting('request.headers', true), '')::jsonb ->> 'accept-profile',
        nullif(current_setting('request.headers', true), '')::jsonb ->> 'content-profile'
    ) IN ('console', 'access') THEN
        PERFORM set_config('statement_timeout', '30s', true);
    END IF;
END;
$$ LANGUAGE plpgsql;
-- +goose StatementEnd

-- +goose StatementBegin
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
    WHERE to_regnamespace(quote_ident(s.name)) IS NULL;

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
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.scopes() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    -- The proxy names a subject by the claim preferred_username. A schema with another claim reads its rows by
    -- another name, so the proxy must not share its entries between subjects.
    IF EXISTS (
        SELECT 1 FROM console.schemas AS s
        WHERE s.claim IS DISTINCT FROM 'preferred_username'
    ) THEN
        RETURN '{}'::jsonb;
    END IF;

    RETURN (
        SELECT coalesce(jsonb_object_agg(subject, digest), '{}'::jsonb)
        FROM (
            SELECT
                subject,
                md5(string_agg(
                    scope::text || '|' || unit_type || '|' || unit_id,
                    E'\n' ORDER BY scope::text COLLATE "C", unit_type COLLATE "C", unit_id COLLATE "C"
                )) AS digest
            FROM access.policy
            GROUP BY subject
        ) AS digests
    );
END
$$;
-- +goose StatementEnd

-- The column access_policy told that the schema had its own table.
DROP VIEW console.health;
CREATE VIEW console.health AS
WITH objects AS (
    SELECT
        n.nspname AS name,
        count(c.oid) FILTER (WHERE c.relkind = 'v')::int AS view_count
    FROM pg_catalog.pg_namespace AS n
    LEFT JOIN pg_catalog.pg_class AS c ON n.oid = c.relnamespace
    GROUP BY n.nspname
)

SELECT
    s.name,
    o.name IS NOT NULL AS schema_exists,
    coalesce(o.view_count, 0) AS view_count
FROM console.schemas AS s
LEFT JOIN objects AS o ON s.name = o.name
WHERE rbac.can('read_sync') AND rbac.mine(s.name);

-- +goose Down
-- noqa: disable=PRS,CP02
DROP VIEW console.health;
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

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.scopes() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_union text;
    v_result jsonb;
BEGIN
    -- The proxy names a subject by the claim preferred_username. A schema with another claim reads its rows by
    -- another name, so the proxy must not share its entries between subjects.
    IF EXISTS (
        SELECT 1 FROM console.schemas AS s
        WHERE to_regclass(format('%I.access_policy', s.name)) IS NOT NULL
            AND s.claim IS DISTINCT FROM 'preferred_username'
    ) THEN
        RETURN '{}'::jsonb;
    END IF;

    SELECT string_agg(
        format('SELECT %L AS schema_name, subject, unit_type, unit_id FROM %I.access_policy', s.name, s.name),
        ' UNION ALL '
    )
    INTO v_union
    FROM console.schemas AS s
    WHERE to_regclass(format('%I.access_policy', s.name)) IS NOT NULL;

    IF v_union IS NULL THEN
        RETURN '{}'::jsonb;
    END IF;

    EXECUTE format(
        $query$
        SELECT coalesce(jsonb_object_agg(subject, scope), '{}'::jsonb)
        FROM (
            SELECT
                subject,
                md5(string_agg(
                    schema_name || '|' || coalesce(unit_type, '') || '|' || coalesce(unit_id, ''),
                    E'\n' ORDER BY schema_name COLLATE "C", unit_type COLLATE "C", unit_id COLLATE "C"
                )) AS scope
            FROM (%s) AS grants
            WHERE subject IS NOT NULL
            GROUP BY subject
        ) AS scopes
        $query$,
        v_union
    ) INTO v_result;

    RETURN v_result;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
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
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION data_proxy.pre_request() RETURNS void AS $$
DECLARE
    claims jsonb := coalesce(
        current_setting('request.jwt.claims', true)::jsonb,
        '{}'::jsonb
    );
    claim_name text;
    claim_value jsonb;
BEGIN
    FOR claim_name, claim_value IN
        SELECT * FROM jsonb_each(claims)
        -- A claim name that is not a plain word, such as allowed-origins from Keycloak, can't be a setting name.
        WHERE key ~ '^[A-Za-z_][A-Za-z0-9_]*$'
    LOOP
        PERFORM set_config(
            'app.claim_' || claim_name,
            CASE jsonb_typeof(claim_value)
                WHEN 'array' THEN (
                    SELECT string_agg(value, ',')
                    FROM jsonb_array_elements_text(claim_value)
                )
                ELSE trim(both '"' FROM claim_value::text)
            END,
            true
        );
    END LOOP;

    PERFORM rbac.load();

    -- The console reads views over the DBOS tables, so its requests may run longer than data requests.
    -- The role of the token has the short timeout, and every token has the same role.
    IF coalesce(
        nullif(current_setting('request.headers', true), '')::jsonb ->> 'accept-profile',
        nullif(current_setting('request.headers', true), '')::jsonb ->> 'content-profile'
    ) = 'console' THEN
        PERFORM set_config('statement_timeout', '30s', true);
    END IF;
END;
$$ LANGUAGE plpgsql;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE PROCEDURE data_proxy.prune_access_log(
    p_retention interval,
    p_schema_name text DEFAULT NULL
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, data_proxy, pg_temp
AS $$
DECLARE
    target_schema text;
BEGIN
    IF p_schema_name IS NULL THEN
        RAISE EXCEPTION 'schema name is required';
    END IF;

    target_schema := p_schema_name;
    EXECUTE format(
        'DELETE FROM %I.access_log WHERE changed_at < now() - $1',
        target_schema
    ) USING p_retention;
END;
$$;
-- +goose StatementEnd

REVOKE ALL ON PROCEDURE data_proxy.prune_access_log(interval, text) FROM PUBLIC;

DROP SCHEMA access CASCADE;
