-- +goose Up
-- Adds projects: the console.projects table seeded with default, the project and
-- short_name columns, the name_schema and keep_name triggers, the project policies, a
-- share lock in console.require_claim(), and a faster console.runs. Squashed from the
-- old console migrations 00011 to 00014.
-- noqa: disable=PRS

-- From console migration 00011_lock_claim_checks
-- noqa: disable=CP02,PRS
-- The check of a row rule and the check of a cleared claim each read what
-- the other has not committed, so two editors could both pass. The share lock
-- on the schema row makes a clear of the claim wait for the rule insert.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.require_claim() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM console.tables AS t
        INNER JOIN console.schemas AS s ON t.schema = s.name
        WHERE t.name = new.table_name AND s.claim IS NOT NULL
        FOR SHARE OF s
    ) THEN
        RAISE EXCEPTION 'The schema of table % has no claim, so it can''t have rls rules',
            new.table_name USING ERRCODE = 'check_violation';
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd

-- From console migration 00012_add_projects
-- noqa: disable=CP02,PRS
-- A project holds schemas. A schema keeps one physical name, the primary key `name`,
-- which the sync, the proxy, the roles and S3 use, so no data moves:
-- `<project>_<short_name>`, or the bare short name in the project `default`. A
-- project name has no underscore, so the first underscore of a name ends the
-- project.
CREATE TABLE console.projects (
    name text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9]{0,29}$'),
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL
);

CREATE TRIGGER stamp BEFORE INSERT OR UPDATE ON console.projects
FOR EACH ROW EXECUTE FUNCTION console.stamp();

INSERT INTO console.projects (name) VALUES ('default');

ALTER TABLE console.schemas
ADD COLUMN project text NOT NULL DEFAULT 'default'
REFERENCES console.projects (name) ON UPDATE CASCADE ON DELETE RESTRICT,
ADD COLUMN short_name text;

-- The backfill is not a change of the configuration: it must not stamp the rows or
-- wake a workflow.
ALTER TABLE console.schemas DISABLE TRIGGER USER;
UPDATE console.schemas SET short_name = name;
ALTER TABLE console.schemas ENABLE TRIGGER USER;

ALTER TABLE console.schemas
ALTER COLUMN short_name SET NOT NULL,
ADD CONSTRAINT schemas_short_name_check CHECK (short_name ~ '^[a-z][a-z0-9_]{0,55}$'),
ADD CONSTRAINT schemas_name_parts CHECK (
    name = CASE
        WHEN project = 'default' THEN short_name
        ELSE project || '_' || short_name
    END
),
ADD CONSTRAINT schemas_project_short_name_key UNIQUE (project, short_name);

-- +goose StatementBegin
CREATE FUNCTION console.name_schema() RETURNS trigger
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
CREATE FUNCTION console.keep_name() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF new.name IS DISTINCT FROM old.name
        OR new.project IS DISTINCT FROM old.project
        OR new.short_name IS DISTINCT FROM old.short_name THEN
        RAISE EXCEPTION 'The name of schema % can''t change: delete it and add it again', old.name
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.name_schema() FROM PUBLIC;
REVOKE ALL ON FUNCTION console.keep_name() FROM PUBLIC;

CREATE TRIGGER name_schema BEFORE INSERT ON console.schemas
FOR EACH ROW EXECUTE FUNCTION console.name_schema();
CREATE TRIGGER keep_name BEFORE UPDATE OF name, project, short_name ON console.schemas
FOR EACH ROW EXECUTE FUNCTION console.keep_name();

-- A token of a project adds a schema that its claim has not expanded into yet, and
-- reads the row that it just added.
DROP POLICY read_scope ON console.schemas;
CREATE POLICY read_scope ON console.schemas FOR SELECT
USING (rbac.can('read_config') AND rbac.mine(name, project));
DROP POLICY insert_scope ON console.schemas;
CREATE POLICY insert_scope ON console.schemas FOR INSERT
WITH CHECK (rbac.can('create_config') AND rbac.mine(name, project));

ALTER TABLE console.projects ENABLE ROW LEVEL SECURITY;

CREATE POLICY read_scope ON console.projects FOR SELECT
USING (
    rbac.can('read_config')
    AND (
        rbac.mine(NULL, console.projects.name)
        OR EXISTS (
            SELECT FROM console.schemas AS s
            WHERE s.project = console.projects.name
        )
    )
);
CREATE POLICY insert_scope ON console.projects FOR INSERT
WITH CHECK (rbac.can('create_config') AND rbac.mine(NULL, name));
CREATE POLICY delete_scope ON console.projects FOR DELETE
USING (rbac.can('delete_config') AND rbac.mine(NULL, name));

-- From console migration 00013_restore_require_claim_definer
-- Migration 00011 replaced console.require_claim() and left out the two
-- settings that 00005 had put on it. A function that is replaced keeps no
-- setting that its new text does not say.
ALTER FUNCTION console.require_claim()
SECURITY DEFINER SET search_path = pg_catalog, pg_temp;

-- From console migration 00014_speed_up_runs_view
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
