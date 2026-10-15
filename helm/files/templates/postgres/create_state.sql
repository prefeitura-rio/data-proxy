{#
{
  "kind": "template",
  "description": "Create the application schema and the tables where the engine keeps the state of each synced table, its errors, and the schemas of each run.",
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

CREATE INDEX IF NOT EXISTS scope_by_run ON {{ schema }}.scope (run_id)
