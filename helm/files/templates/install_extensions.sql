{#
{
  "kind": "template",
  "description": "Install postgis, pg_duckdb, pg_stat_statements, sqlite, and ducklake extensions, then create the rls schema."
}
#}
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_duckdb;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT duckdb.install_extension('sqlite');
SELECT duckdb.install_extension('ducklake');
CREATE SCHEMA IF NOT EXISTS rls
