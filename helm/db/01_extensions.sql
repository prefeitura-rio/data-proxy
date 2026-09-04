-- This script installs the database extensions.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_duckdb;
SET duckdb.force_execution = true;
CREATE SCHEMA IF NOT EXISTS rls;
DO $$
BEGIN
    CREATE TYPE rls.sync_status AS ENUM ('success', 'failure');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
