-- +goose Up
-- noqa: disable=CP02,PRS
-- +goose StatementBegin
CREATE FUNCTION admin.activate_revision(revision bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    target data_proxy.revisions;
BEGIN
    SELECT * INTO target FROM data_proxy.revisions WHERE id = revision FOR UPDATE;

    IF NOT found THEN
        RAISE EXCEPTION 'Revision % does not exist', revision USING ERRCODE = 'PT404';
    END IF;

    IF target.valid IS NOT TRUE THEN
        RAISE EXCEPTION 'Revision % has not passed validation', revision
            USING ERRCODE = 'PT400';
    END IF;

    UPDATE data_proxy.revisions SET active = FALSE WHERE active AND id <> revision;
    UPDATE data_proxy.revisions SET active = TRUE, activated_at = now() WHERE id = revision;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION admin.activate_revision(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.activate_revision(bigint) TO console_admin;

-- +goose Down
DROP FUNCTION admin.activate_revision(bigint);
