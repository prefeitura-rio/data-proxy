-- This script installs the pg_duckdb extension.
CREATE EXTENSION IF NOT EXISTS pg_duckdb;
CREATE SCHEMA IF NOT EXISTS rls;
DO $$
BEGIN
    CREATE TYPE rls.sync_status AS ENUM ('success', 'failure');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
