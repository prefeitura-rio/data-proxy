-- +goose Up
-- Creates the data_proxy schema with the state, errors and scope tables, and
-- data_proxy.sees(), which decides whether a token may see a workflow. sees() calls
-- the rbac schema only when it runs.
CREATE SCHEMA IF NOT EXISTS data_proxy;

CREATE TABLE IF NOT EXISTS data_proxy.state (
    table_name text PRIMARY KEY,
    state jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS data_proxy.errors (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reason text NOT NULL,
    fields jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS errors_by_time ON data_proxy.errors (recorded_at DESC);

CREATE TABLE IF NOT EXISTS data_proxy.scope (
    workflow_id text NOT NULL,
    run_id text NOT NULL,
    schema_name text NOT NULL,
    table_name text NOT NULL DEFAULT '',
    PRIMARY KEY (workflow_id, schema_name, table_name)
);

CREATE INDEX IF NOT EXISTS scope_by_run ON data_proxy.scope (run_id);

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION data_proxy.sees(
    workflow text, partition_key text
) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RETURN rbac.mine(partition_key) OR EXISTS (
        SELECT FROM data_proxy.scope AS s
        WHERE s.workflow_id = workflow AND rbac.mine(s.schema_name)
    );
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION data_proxy.sees(text, text) FROM public;
