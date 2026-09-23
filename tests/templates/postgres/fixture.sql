CREATE EXTENSION IF NOT EXISTS pg_duckdb;
CREATE SCHEMA rls;
CREATE TYPE rls.sync_status AS ENUM ('success', 'failure');
CREATE ROLE "user" NOLOGIN;
CREATE ROLE authenticator NOLOGIN;
CREATE ROLE anon NOLOGIN;
