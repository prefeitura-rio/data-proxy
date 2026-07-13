-- Single low-privilege role for every request. NOBYPASSRLS is the default for
-- non-superuser roles but is spelled out here because it's the single most
-- security-critical attribute this role has: without it, RLS policies below
-- would be silently skipped. See docs/architecture-decisions.md ("Role model").
CREATE ROLE web_anon NOLOGIN NOBYPASSRLS;

-- PostgREST's own connection role (PGRST_DB_URI). PostgREST switches into
-- web_anon per-request via SET ROLE/SET LOCAL ROLE internally; authenticator
-- itself never touches application tables directly.
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD 'authenticator';
GRANT web_anon TO authenticator;

-- PGRST_DB_SCHEMA in docker-compose.yml -- only tables/views/functions in
-- this schema are exposed by PostgREST.
CREATE SCHEMA IF NOT EXISTS api;
GRANT USAGE ON SCHEMA api TO web_anon;
