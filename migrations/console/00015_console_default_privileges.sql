-- +goose Up
-- noqa: disable=RF05
-- Sets default privileges for the migration owner (PGUSER) in the console schema, so a
-- later table is readable and a later function is executable by the token role
-- (AUTH_USER_ROLE). A column grant stays in the migration that creates the table.

-- Goose fills ${...} only between ENVSUB ON and ENVSUB OFF, and it turns a dollar sign
-- there into a plain one, so keep a $$ body outside. Each name is quoted, so a name
-- that is not set stops the run.
-- +goose ENVSUB ON
ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}" IN SCHEMA console
GRANT SELECT ON TABLES TO "${AUTH_USER_ROLE}";

ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}" IN SCHEMA console
GRANT EXECUTE ON FUNCTIONS TO "${AUTH_USER_ROLE}";
-- +goose ENVSUB OFF

-- +goose Down
-- +goose ENVSUB ON
ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}" IN SCHEMA console
REVOKE EXECUTE ON FUNCTIONS FROM "${AUTH_USER_ROLE}";

ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}" IN SCHEMA console
REVOKE SELECT ON TABLES FROM "${AUTH_USER_ROLE}";
-- +goose ENVSUB OFF
