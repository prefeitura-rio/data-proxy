-- +goose Up
-- noqa: disable=PRS,CP02
-- Gives every schema the physical name `<project>__<schema>`, also in the project
-- default, and renames the schema, the state, the errors, the scope and the history.
-- It deletes the schedules, because DBOS pickles their schema list, and queues the
-- reconcile that creates them again and a sync run that rebuilds the generated
-- objects. The old name stays the folder of the files in S3, so no file moves. A
-- unique index on the old name keeps two schemas out of one folder.

CREATE UNIQUE INDEX schemas_data_prefix ON console.schemas (
    (
        CASE
            WHEN project = 'default' THEN short_name
            ELSE project || '_' || short_name
        END
    )
);

-- The helper renames in both directions. A later migration drops it.
-- +goose StatementBegin
CREATE FUNCTION data_proxy.rename_schemas(p_forward boolean) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_schema record;
BEGIN
    ALTER TABLE console.schemas DISABLE TRIGGER USER;
    ALTER TABLE console.tables DISABLE TRIGGER USER;
    ALTER TABLE console.schemas DROP CONSTRAINT schemas_name_parts;

    FOR v_schema IN
        SELECT
            s.name AS old,
            CASE WHEN p_forward THEN s.project || '__' || s.short_name
                WHEN s.project = 'default' THEN s.short_name
                ELSE s.project || '_' || s.short_name
            END AS new
        FROM console.schemas AS s
    LOOP
        CONTINUE WHEN v_schema.old = v_schema.new;
        UPDATE console.schemas SET name = v_schema.new WHERE name = v_schema.old;
        UPDATE console.config_history SET schema_name = v_schema.new WHERE schema_name = v_schema.old;
        UPDATE console.config_history SET key = v_schema.new WHERE object = 'schemas' AND key = v_schema.old;
        UPDATE data_proxy.scope SET schema_name = v_schema.new WHERE schema_name = v_schema.old;
        UPDATE data_proxy.state
        SET table_name = v_schema.new || substr(table_name, length(v_schema.old) + 1)
        WHERE split_part(table_name, '.', 1) = v_schema.old AND strpos(table_name, '.') > 0;
        UPDATE data_proxy.errors
        SET fields = jsonb_set(fields, '{schema}', to_jsonb(v_schema.new))
        WHERE fields ->> 'schema' = v_schema.old;
        UPDATE data_proxy.errors
        SET fields = jsonb_set(
            fields, '{table}', to_jsonb(v_schema.new || substr(fields ->> 'table', length(v_schema.old) + 1))
        )
        WHERE split_part(fields ->> 'table', '.', 1) = v_schema.old AND strpos(fields ->> 'table', '.') > 0;
        DELETE FROM dbos.workflow_schedules WHERE schedule_name = 'sync-' || v_schema.old;

        IF to_regnamespace(quote_ident(v_schema.old)) IS NOT NULL
            AND to_regnamespace(quote_ident(v_schema.new)) IS NULL THEN
            EXECUTE format('ALTER SCHEMA %I RENAME TO %I', v_schema.old, v_schema.new);
        END IF;
    END LOOP;

    IF p_forward THEN
        ALTER TABLE console.schemas ADD CONSTRAINT schemas_name_parts
        CHECK (name = project || '__' || short_name);
    ELSE
        ALTER TABLE console.schemas ADD CONSTRAINT schemas_name_parts CHECK (
            name = CASE WHEN project = 'default' THEN short_name ELSE project || '_' || short_name END
        );
    END IF;

    ALTER TABLE console.tables ENABLE TRIGGER USER;
    ALTER TABLE console.schemas ENABLE TRIGGER USER;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION data_proxy.rename_schemas(boolean) FROM PUBLIC;

SELECT data_proxy.rename_schemas(TRUE);

-- The functions and the views of each schema still name the old schema. A sync run
-- rebuilds them, because their signature changed with the name, so the migration
-- queues one run for every schema instead of waiting for the next schedule.
-- +goose StatementBegin
DO $enqueue$
BEGIN
    IF EXISTS (SELECT FROM console.schemas) THEN
        PERFORM dbos.enqueue_workflow(
            workflow_name => 'reconcile_schedules', queue_name => 'schedules'
        );
        PERFORM dbos.enqueue_workflow(
            workflow_name => 'run_sync',
            queue_name => 'sync',
            positional_args => ARRAY[
                to_json(now()),
                json_build_object(
                    'schemas', (SELECT array_agg(name ORDER BY name) FROM console.schemas),
                    'tables', ARRAY[]::text[]
                )
            ]
        );
    END IF;
END
$enqueue$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.name_schema() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF new.name IS NULL THEN
        IF new.short_name IS NULL THEN
            RAISE EXCEPTION 'A schema needs a name, or a project and a short name'
                USING ERRCODE = 'check_violation';
        END IF;
        new.name := new.project || '__' || new.short_name;
    ELSIF new.short_name IS NULL AND starts_with(new.name, new.project || '__') THEN
        new.short_name := substr(new.name, length(new.project) + 3);
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.import_schemas(document jsonb)
RETURNS TABLE (project text, short_name text, name text, body jsonb)
LANGUAGE sql IMMUTABLE AS $$
    SELECT i.project, i.short_name, i.project || '__' || i.short_name, i.body
    FROM (
        SELECT
            CASE WHEN strpos(s.key, '__') > 0 THEN split_part(s.key, '__', 1) ELSE 'default' END AS project,
            CASE WHEN strpos(s.key, '__') > 0 THEN substr(s.key, strpos(s.key, '__') + 2) ELSE s.key END AS short_name,
            s.value AS body
        FROM jsonb_each(coalesce(document -> 'schemas', '{}'::jsonb)) AS s
        UNION ALL
        SELECT p.key, s.key, s.value
        FROM jsonb_each(coalesce(document -> 'projects', '{}'::jsonb)) AS p
        CROSS JOIN LATERAL jsonb_each(coalesce(p.value -> 'schemas', '{}'::jsonb)) AS s
    ) AS i
$$;
-- +goose StatementEnd

-- A claim entry `project.schema` or `project.schema:*` names the physical schema.
-- An entry in the old form matches no schema.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.load() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_schemas text := replace(coalesce(current_setting('app.claim_schemas', TRUE), ''), '.', '__');
    v_projects text := coalesce(current_setting('app.claim_projects', TRUE), '');
    v_expanded text;
BEGIN
    PERFORM set_config(
        'rbac.permissions',
        coalesce(
            (SELECT wrapped FROM rbac.roles WHERE name = current_setting('app.claim_role', TRUE)),
            ''
        ),
        TRUE
    );
    PERFORM set_config(
        'rbac.projects',
        CASE WHEN v_projects = '' THEN '' ELSE ',' || v_projects || ',' END,
        TRUE
    );

    -- A project entry gives the schemas that the project has now: `<project>` gives `<schema>`, and
    -- `<project>:*` gives `<schema>:*`. The expansion runs for each request, so a schema that comes later is in it.
    IF v_projects <> '' THEN
        SELECT string_agg(item, ',' ORDER BY item COLLATE "C")
        INTO v_expanded
        FROM (
            SELECT DISTINCT
                CASE
                    WHEN c.entry = '*' THEN '*'
                    WHEN c.entry LIKE '%:*' THEN s.name || ':*'
                    ELSE s.name
                END AS item
            FROM unnest(string_to_array(v_projects, ',')) AS c (entry)
            LEFT JOIN console.schemas AS s ON s.project = regexp_replace(c.entry, ':\*$', '')
            WHERE c.entry = '*' OR s.name IS NOT NULL
        ) AS items;
    END IF;

    v_schemas := concat_ws(',', nullif(v_schemas, ''), nullif(v_expanded, ''));
    PERFORM set_config(
        'rbac.schemas',
        CASE WHEN v_schemas = '' THEN '' ELSE ',' || v_schemas || ',' END,
        TRUE
    );
END
$$;
-- +goose StatementEnd

-- +goose Down
-- noqa: disable=PRS,CP02
SELECT data_proxy.rename_schemas(FALSE);

-- +goose StatementBegin
DO $enqueue$
BEGIN
    IF EXISTS (SELECT FROM console.schemas) THEN
        PERFORM dbos.enqueue_workflow(
            workflow_name => 'reconcile_schedules', queue_name => 'schedules'
        );
    END IF;
END
$enqueue$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.name_schema() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF new.name IS NULL THEN
        IF new.short_name IS NULL THEN
            RAISE EXCEPTION 'A schema needs a name, or a project and a short name'
                USING ERRCODE = 'check_violation';
        END IF;
        new.name := CASE
            WHEN new.project = 'default' THEN new.short_name
            ELSE new.project || '_' || new.short_name
        END;
    ELSIF new.short_name IS NULL THEN
        new.short_name := CASE
            WHEN new.project = 'default' THEN new.name
            WHEN starts_with(new.name, new.project || '_') THEN substr(new.name, length(new.project) + 2)
        END;
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.import_schemas(document jsonb)
RETURNS TABLE (project text, short_name text, name text, body jsonb)
LANGUAGE sql IMMUTABLE AS $$
    SELECT
        i.project,
        i.short_name,
        CASE WHEN i.project = 'default' THEN i.short_name ELSE i.project || '_' || i.short_name END,
        i.body
    FROM (
        SELECT 'default' AS project, s.key AS short_name, s.value AS body
        FROM jsonb_each(coalesce(document -> 'schemas', '{}'::jsonb)) AS s
        UNION ALL
        SELECT p.key, s.key, s.value
        FROM jsonb_each(coalesce(document -> 'projects', '{}'::jsonb)) AS p
        CROSS JOIN LATERAL jsonb_each(coalesce(p.value -> 'schemas', '{}'::jsonb)) AS s
    ) AS i
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.load() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_schemas text := coalesce(current_setting('app.claim_schemas', TRUE), '');
    v_projects text := coalesce(current_setting('app.claim_projects', TRUE), '');
    v_expanded text;
BEGIN
    PERFORM set_config(
        'rbac.permissions',
        coalesce(
            (SELECT wrapped FROM rbac.roles WHERE name = current_setting('app.claim_role', TRUE)),
            ''
        ),
        TRUE
    );
    PERFORM set_config(
        'rbac.projects',
        CASE WHEN v_projects = '' THEN '' ELSE ',' || v_projects || ',' END,
        TRUE
    );

    -- A project entry gives the schemas that the project has now: `<project>` gives `<schema>`, and
    -- `<project>:*` gives `<schema>:*`. The expansion runs for each request, so a schema that comes later is in it.
    IF v_projects <> '' THEN
        SELECT string_agg(item, ',' ORDER BY item COLLATE "C")
        INTO v_expanded
        FROM (
            SELECT DISTINCT
                CASE
                    WHEN c.entry = '*' THEN '*'
                    WHEN c.entry LIKE '%:*' THEN s.name || ':*'
                    ELSE s.name
                END AS item
            FROM unnest(string_to_array(v_projects, ',')) AS c (entry)
            LEFT JOIN console.schemas AS s ON s.project = regexp_replace(c.entry, ':\*$', '')
            WHERE c.entry = '*' OR s.name IS NOT NULL
        ) AS items;
    END IF;

    v_schemas := concat_ws(',', nullif(v_schemas, ''), nullif(v_expanded, ''));
    PERFORM set_config(
        'rbac.schemas',
        CASE WHEN v_schemas = '' THEN '' ELSE ',' || v_schemas || ',' END,
        TRUE
    );
END
$$;
-- +goose StatementEnd

DROP FUNCTION data_proxy.rename_schemas(boolean);
DROP INDEX console.schemas_data_prefix;
