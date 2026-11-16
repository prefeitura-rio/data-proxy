-- +goose Up
-- noqa: disable=RF05
-- Sets default privileges so the catalog reader duckdb can use every schema and read
-- every table that the owner (PGUSER) creates later. It runs first, so later catalog
-- files get the same rights.

-- Goose fills ${...} only between ENVSUB ON and ENVSUB OFF, and it turns a dollar sign
-- there into a plain one, so keep a $$ body outside. Each name is quoted, so a name
-- that is not set stops the run.
-- +goose ENVSUB ON
ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}"
GRANT USAGE ON SCHEMAS TO duckdb;

ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}"
GRANT SELECT ON TABLES TO duckdb;
-- +goose ENVSUB OFF

-- +goose Down
-- +goose ENVSUB ON
ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}"
REVOKE SELECT ON TABLES FROM duckdb;

ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}"
REVOKE USAGE ON SCHEMAS FROM duckdb;
-- +goose ENVSUB OFF
