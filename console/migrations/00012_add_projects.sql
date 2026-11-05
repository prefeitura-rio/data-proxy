-- +goose Up
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

-- +goose Down
DROP POLICY read_scope ON console.projects;
DROP POLICY insert_scope ON console.schemas;
CREATE POLICY insert_scope ON console.schemas FOR INSERT
WITH CHECK (rbac.can('create_config') AND rbac.mine(name));
DROP POLICY read_scope ON console.schemas;
CREATE POLICY read_scope ON console.schemas FOR SELECT
USING (rbac.can('read_config') AND rbac.mine(name));

DROP TRIGGER keep_name ON console.schemas;
DROP TRIGGER name_schema ON console.schemas;
DROP FUNCTION console.keep_name();
DROP FUNCTION console.name_schema();

ALTER TABLE console.schemas
DROP COLUMN short_name,
DROP COLUMN project;

DROP TABLE console.projects;
