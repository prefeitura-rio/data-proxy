-- +goose Up
-- noqa: disable=PRS

-- Creates the rbac schema: the rbac.revision counter, the rbac.roles table, and the
-- functions rbac.unknown(), mine(), all_rows(), load() and can(). Until load_rbac.sql
-- loads rbac.toml and replaces it, rbac.can() raises an error for every permission.
CREATE SCHEMA IF NOT EXISTS rbac;

-- A row, not a sequence: a standby replays a sequence only every 32 values, so it would miss changes.
CREATE TABLE IF NOT EXISTS rbac.revision (
    singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    value bigint NOT NULL DEFAULT 0
);

INSERT INTO rbac.revision DEFAULT VALUES ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS rbac.roles (
    name text PRIMARY KEY,
    permissions text[] NOT NULL,
    wrapped text NOT NULL
);

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.unknown(feature text) RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
BEGIN
    RAISE EXCEPTION 'Unknown permission: %', feature USING ERRCODE = 'PT500';
END
$$;
-- +goose StatementEnd

-- The one rule for "is it mine": a schema of the schemas claim, or a project of the projects claim.
-- A projects claim has already expanded into the schemas that exist, so a policy that checks a schema
-- that does not exist yet, such as an insert, passes the project of the new row as the second argument.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.mine(schema_name text, project_name text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT coalesce(
        (
            schema_name <> ''
            AND strpos(schema_name, ',') = 0
            AND (
                strpos(
                    coalesce(current_setting('rbac.schemas', TRUE), ''),
                    ',' || schema_name || ','
                ) > 0
                OR strpos(
                    coalesce(current_setting('rbac.schemas', TRUE), ''),
                    ',' || schema_name || ':*,'
                ) > 0
                OR strpos(
                    coalesce(current_setting('rbac.schemas', TRUE), ''),
                    ',*,'
                ) > 0
            )
        )
        OR (
            project_name <> ''
            AND strpos(project_name, ',') = 0
            AND (
                strpos(
                    coalesce(current_setting('rbac.projects', TRUE), ''),
                    ',' || project_name || ','
                ) > 0
                OR strpos(
                    coalesce(current_setting('rbac.projects', TRUE), ''),
                    ',' || project_name || ':*,'
                ) > 0
                OR strpos(
                    coalesce(current_setting('rbac.projects', TRUE), ''),
                    ',*,'
                ) > 0
                OR strpos(
                    coalesce(current_setting('rbac.schemas', TRUE), ''),
                    ',*,'
                ) > 0
            )
        ),
        FALSE
    )
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.mine(schema_name text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT rbac.mine(schema_name, NULL)
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.all_rows(schema_name text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT coalesce(
        schema_name <> ''
        AND strpos(schema_name, ',') = 0
        AND (
            strpos(
                coalesce(current_setting('rbac.schemas', TRUE), ''),
                ',' || schema_name || ':*,'
            ) > 0
            OR strpos(
                coalesce(current_setting('rbac.schemas', TRUE), ''),
                ',*,'
            ) > 0
        ),
        FALSE
    )
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

REVOKE ALL ON FUNCTION rbac.unknown(text), rbac.mine(text), rbac.mine(text, text), rbac.all_rows(text), rbac.load() FROM PUBLIC;

-- The rules load replaces this function with one that lists the known permissions.
-- Until then every permission is unknown, so a policy that asks for one raises an error.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.can(feature text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT rbac.unknown(feature)
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION rbac.can(text) FROM PUBLIC;
