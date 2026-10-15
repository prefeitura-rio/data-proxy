{#
{
  "kind": "template",
  "description": "Create the extension, schema, and roles that every test database needs."
}
#}
CREATE EXTENSION IF NOT EXISTS pg_duckdb;
CREATE ROLE "user" NOLOGIN; -- noqa: RF06
CREATE ROLE authenticator NOLOGIN;
CREATE ROLE anon NOLOGIN;
-- CNPG creates policy_writer and makes the authenticator a member.
CREATE ROLE policy_writer NOLOGIN;
GRANT policy_writer TO authenticator;
