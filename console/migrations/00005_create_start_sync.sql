-- +goose Up
-- noqa: disable=CP02,PRS
-- +goose StatementBegin
CREATE FUNCTION admin.start_sync(
    schemas text[] DEFAULT '{}', tables text[] DEFAULT '{}'
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RETURN dbos.enqueue_workflow(
        workflow_name => 'run_sync',
        queue_name => 'sync',
        positional_args => ARRAY[
            to_json(now()),
            json_build_object('schemas', schemas, 'tables', tables)
        ],
        authenticated_user => admin.username()
    );
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION admin.start_sync(text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.start_sync(text[], text[]) TO console_operator;

-- +goose Down
DROP FUNCTION admin.start_sync(text[], text[]);
