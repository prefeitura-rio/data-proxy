-- +goose Up
-- noqa: disable=CP02,PRS
-- The engine generates dl_table_files(table) in every schema, with the data
-- location of that schema. This function checks the token and calls it.
-- An editor or an admin can read the raw files of its schemas, with every row,
-- so the browser can query them. A name outside the token and a name that
-- doesn't exist get the same refusal.
-- +goose StatementBegin
CREATE FUNCTION admin.table_files(schema_name text, table_name text)
RETURNS TABLE (path text, size_bytes bigint, delete_file text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF NOT admin.mine(schema_name) OR NOT EXISTS (
        SELECT FROM admin.tables AS t
        WHERE t.schema = schema_name AND t.name = table_name
    ) THEN
        RAISE EXCEPTION 'The token has no access to this table'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF EXISTS (SELECT FROM admin.schemas AS s WHERE s.name = schema_name AND s.encrypted) THEN
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

REVOKE ALL ON FUNCTION admin.table_files(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.table_files(text, text) TO console_editor;

-- +goose Down
DROP FUNCTION admin.table_files(text, text);
