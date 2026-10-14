-- +goose Up
-- noqa: disable=CP02,PRS
CREATE FUNCTION admin.username() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT coalesce(c ->> 'preferred_username', c ->> 'sub', current_user)
    FROM (SELECT nullif(current_setting('request.jwt.claims', TRUE), '')::jsonb AS c) AS t
$$;

REVOKE ALL ON FUNCTION admin.username() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.username() TO console_viewer;

-- +goose Down
DROP FUNCTION admin.username();
