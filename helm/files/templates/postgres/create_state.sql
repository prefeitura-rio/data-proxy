{#
{
  "kind": "template",
  "description": "Create the application schema, the tables where the engine keeps the state of each synced table, its errors, and the schemas of each run, and the function that says which workflows a token sees. The function calls the rbac schema when it runs.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema that holds application state."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE TABLE IF NOT EXISTS {{ schema }}.state (
    table_name text PRIMARY KEY,
    state jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS {{ schema }}.errors (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reason text NOT NULL,
    fields jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {{ schema }}.scope (
    workflow_id text NOT NULL,
    run_id text NOT NULL,
    schema_name text NOT NULL,
    table_name text NOT NULL DEFAULT '',
    PRIMARY KEY (workflow_id, schema_name, table_name)
);

CREATE INDEX IF NOT EXISTS scope_by_run ON {{ schema }}.scope (run_id);

CREATE OR REPLACE FUNCTION {{ schema }}.sees(
    workflow text, partition_key text
) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RETURN rbac.mine(partition_key) OR EXISTS (
        SELECT FROM {{ schema }}.scope AS s
        WHERE s.workflow_id = workflow AND rbac.mine(s.schema_name)
    );
END
$$;

REVOKE ALL ON FUNCTION {{ schema }}.sees(text, text) FROM public
