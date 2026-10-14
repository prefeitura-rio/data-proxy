-- +goose Up
-- noqa: disable=CP02,PRS
CREATE FUNCTION admin.username() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT coalesce(c ->> 'preferred_username', c ->> 'sub', current_user)
    FROM (SELECT nullif(current_setting('request.jwt.claims', TRUE), '')::jsonb AS c) AS t
$$;

REVOKE ALL ON FUNCTION admin.username() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.username() TO console_viewer;

ALTER TABLE data_proxy.revisions ALTER COLUMN author SET DEFAULT admin.username();

-- +goose StatementBegin
CREATE FUNCTION data_proxy.clear_validation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.valid := NULL;
    NEW.errors := NULL;
    NEW.validated_at := NULL;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

CREATE TRIGGER clear_validation
BEFORE UPDATE OF document ON data_proxy.revisions
FOR EACH ROW
WHEN (old.document IS DISTINCT FROM new.document)
EXECUTE FUNCTION data_proxy.clear_validation();

CREATE VIEW admin.drafts AS
SELECT
    id,
    created_at,
    author,
    comment,
    document,
    valid,
    errors,
    validated_at
FROM data_proxy.revisions
WHERE activated_at IS NULL
WITH CHECK OPTION;

GRANT INSERT (comment, document), UPDATE (comment, document), DELETE
ON admin.drafts TO console_editor;

-- +goose Down
DROP VIEW admin.drafts;
DROP TRIGGER clear_validation ON data_proxy.revisions;
DROP FUNCTION data_proxy.clear_validation();
ALTER TABLE data_proxy.revisions ALTER COLUMN author DROP DEFAULT;
DROP FUNCTION admin.username();
