CREATE SCHEMA IF NOT EXISTS ${schema};
GRANT USAGE ON SCHEMA ${schema} TO web_anon;
GRANT USAGE ON SCHEMA ${schema} TO authenticator
