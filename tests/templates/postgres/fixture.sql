{#
{
  "kind": "template",
  "description": "Create the extension, schema, and roles that every test database needs."
}
#}
CREATE EXTENSION IF NOT EXISTS pg_duckdb;
CREATE SCHEMA rls;
CREATE ROLE "user" NOLOGIN;
CREATE ROLE authenticator NOLOGIN;
CREATE ROLE anon NOLOGIN;
