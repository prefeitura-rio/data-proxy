{#
{
  "kind": "template",
  "description": "Install postgis, pg_duckdb, pg_stat_statements, sqlite, and ducklake extensions, then create the rls schema and sync_status enum."
}
#}
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_duckdb;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT duckdb.install_extension('sqlite');
SELECT duckdb.install_extension('ducklake');
CREATE SCHEMA IF NOT EXISTS rls;
DO $$
BEGIN
    CREATE TYPE rls.sync_status AS ENUM ('success', 'failure');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$
