-- +goose Up
-- noqa: disable=RF05
-- Grants the request roles USAGE on data_proxy and console, EXECUTE on the functions,
-- SELECT on the console tables, column-level INSERT and UPDATE on the configuration
-- tables, and DELETE on projects, schemas, tables and rls. The role names come from
-- AUTH_ANON_ROLE and AUTH_USER_ROLE.

-- Goose fills ${...} only between ENVSUB ON and ENVSUB OFF, and it turns a dollar sign
-- there into a plain one, so keep a $$ body outside. Each name is quoted, so a name
-- that is not set stops the run.
-- +goose ENVSUB ON
GRANT USAGE ON SCHEMA data_proxy TO "${AUTH_ANON_ROLE}";

GRANT USAGE ON SCHEMA data_proxy TO "${AUTH_USER_ROLE}";

GRANT EXECUTE ON FUNCTION data_proxy.pre_request() TO "${AUTH_ANON_ROLE}";

GRANT EXECUTE ON FUNCTION data_proxy.pre_request() TO "${AUTH_USER_ROLE}";

GRANT EXECUTE ON FUNCTION data_proxy.sees(text, text) TO "${AUTH_USER_ROLE}";

GRANT USAGE ON SCHEMA console TO "${AUTH_ANON_ROLE}";

GRANT EXECUTE ON FUNCTION console.config() TO "${AUTH_ANON_ROLE}";

GRANT USAGE ON SCHEMA console TO "${AUTH_USER_ROLE}";

-- The console rights are wide on purpose: every function and row policy checks the
-- permission of the token, and the schemas claim limits the rows.
GRANT SELECT ON ALL TABLES IN SCHEMA console TO "${AUTH_USER_ROLE}";

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA console TO "${AUTH_USER_ROLE}";

GRANT INSERT (name, project, short_name, source, settings, claim, encrypted),
UPDATE (source, settings, claim, encrypted)
ON console.schemas TO "${AUTH_USER_ROLE}";

GRANT INSERT (name, schema, strategy, n, fallback, cache_ttl, partitioning, sort),
UPDATE (strategy, n, fallback, cache_ttl, partitioning, sort)
ON console.tables TO "${AUTH_USER_ROLE}";

GRANT INSERT (table_name, column_name, unit_type),
UPDATE (column_name, unit_type)
ON console.rls TO "${AUTH_USER_ROLE}";

GRANT INSERT (name) ON console.projects TO "${AUTH_USER_ROLE}";

GRANT DELETE
ON console.projects, console.schemas, console.tables, console.rls
TO "${AUTH_USER_ROLE}";
-- +goose ENVSUB OFF
