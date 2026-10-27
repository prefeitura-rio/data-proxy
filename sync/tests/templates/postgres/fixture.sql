{#
{
  "kind": "template",
  "description": "Create the extension, schema, and roles that every test database needs."
}
#}
CREATE EXTENSION IF NOT EXISTS pg_duckdb;
CREATE ROLE "user" NOLOGIN; -- noqa: RF06
CREATE ROLE serving NOLOGIN;
CREATE ROLE anon NOLOGIN;
CREATE ROLE dp_api NOLOGIN;
CREATE ROLE dp_policy NOLOGIN;
CREATE ROLE dp_console NOLOGIN;
CREATE ROLE dp_admin NOLOGIN;
CREATE ROLE dp_p0 NOLOGIN;
CREATE ROLE dp_p1 NOLOGIN;
CREATE ROLE dp_p2 NOLOGIN;
CREATE ROLE dp_p3 NOLOGIN;
CREATE ROLE dp_p4 NOLOGIN;
CREATE ROLE dp_p5 NOLOGIN;
CREATE ROLE dp_p6 NOLOGIN;
CREATE ROLE dp_p7 NOLOGIN;
