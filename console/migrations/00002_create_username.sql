-- +goose Up
-- noqa: disable=CP02,PRS
CREATE FUNCTION console.username() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT coalesce(c ->> 'preferred_username', c ->> 'sub', current_user)
    FROM (SELECT nullif(current_setting('request.jwt.claims', TRUE), '')::jsonb AS c) AS t
$$;

REVOKE ALL ON FUNCTION console.username() FROM PUBLIC;

-- +goose Down
DROP FUNCTION console.username();
