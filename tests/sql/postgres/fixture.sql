CREATE SCHEMA app;
CREATE SCHEMA rls;
CREATE TYPE rls.sync_status AS ENUM ('success', 'failure');
CREATE ROLE "user" NOLOGIN;
CREATE ROLE authenticator NOLOGIN;
CREATE ROLE anon NOLOGIN;
CREATE TABLE app.freshness (
    "table" text NOT NULL,
    strategy text NOT NULL,
    partition text,
    updated_at timestamptz,
    attempted_at timestamptz NOT NULL,
    status rls.sync_status NOT NULL,
    UNIQUE NULLS NOT DISTINCT ("table", strategy, partition)
);
