-- +goose Up
-- noqa: disable=CP02,PRS
-- +goose StatementBegin
CREATE FUNCTION data_proxy.enqueue_validation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    PERFORM dbos.enqueue_workflow(
        workflow_name => 'validate_revision',
        queue_name => 'validate',
        positional_args => ARRAY[to_json(new.id)]
    );
    RETURN NULL;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION data_proxy.enqueue_validation() FROM PUBLIC;

CREATE TRIGGER enqueue_validation
AFTER INSERT OR UPDATE OF document ON data_proxy.revisions
FOR EACH ROW
WHEN (new.valid IS NULL AND new.activated_at IS NULL)
EXECUTE FUNCTION data_proxy.enqueue_validation();

-- +goose Down
DROP TRIGGER enqueue_validation ON data_proxy.revisions;
DROP FUNCTION data_proxy.enqueue_validation();
