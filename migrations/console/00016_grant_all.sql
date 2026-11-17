-- +goose Up
-- noqa: disable=PRS
-- Makes rbac.mine(schema, project) true for a role that holds the permission
-- grant_all, which dp_admin has now, when the name is a whole schema or a whole
-- project. It reads the setting and doesn't call rbac.can(), so the function still
-- inlines to strpos. Every other role keeps the claims as the only way to reach a
-- schema. The Down part restores the body of 00005.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.mine(
    schema_name text, project_name text
) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT coalesce(
        (
            strpos(
                coalesce(current_setting('rbac.permissions', TRUE), ''),
                ',grant_all,'
            ) > 0
            AND (
                (schema_name <> '' AND strpos(schema_name, ',') = 0)
                OR (project_name <> '' AND strpos(project_name, ',') = 0)
            )
        )
        OR (
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

-- +goose Down
-- noqa: disable=PRS
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION rbac.mine(
    schema_name text, project_name text
) RETURNS boolean
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
