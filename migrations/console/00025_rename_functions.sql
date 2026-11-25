-- +goose Up
-- noqa: disable=PRS,CP02
-- Renames the functions of the data schemas and rbac.all_rows() to shorter names.
-- The sync creates `snapshot()`, `files(text)` and `changes(text, bigint, bigint)`.
-- This migration drops `dl_latest_snapshot()`, `dl_table_files(text)` and
-- `dl_chg(text, bigint, bigint)` of the console schemas. It renames rbac.all_rows()
-- to rbac.whole(), which keeps the grants, and points console.table_files() at the
-- new name.

-- +goose StatementBegin
DO $drop$
DECLARE
    v_function record;
BEGIN
    FOR v_function IN
        SELECT p.oid::regprocedure::text AS signature
        FROM pg_catalog.pg_proc AS p
        INNER JOIN pg_catalog.pg_namespace AS n ON p.pronamespace = n.oid
        INNER JOIN console.schemas AS s ON n.nspname = s.name
        WHERE
            (p.proname = 'dl_latest_snapshot' AND p.pronargs = 0)
            OR (p.proname = 'dl_table_files' AND p.pronargs = 1)
            OR (p.proname = 'dl_chg' AND p.pronargs = 3)
    LOOP
        EXECUTE format('DROP FUNCTION %s', v_function.signature);
    END LOOP;
END
$drop$;
-- +goose StatementEnd

ALTER FUNCTION rbac.all_rows(text) RENAME TO whole;

-- +goose StatementBegin
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
        'SELECT f.path, f.size_bytes, f.delete_file FROM %I.files($1) AS f',
        schema_name
    ) USING regexp_replace(table_name, '^.*\.', '');
END
$$;
-- +goose StatementEnd

-- +goose Down
-- noqa: disable=PRS,CP02
-- The Down part restores the old names of the rbac function and of the console
-- function. It leaves the data schemas alone, because the sync of the old release
-- creates the old functions.
ALTER FUNCTION rbac.whole(text) RENAME TO all_rows;

-- +goose StatementBegin
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
-- +goose StatementEnd
