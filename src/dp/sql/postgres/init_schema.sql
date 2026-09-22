{#
{
  "kind": "template",
  "description": "Create the application state schema and its state and errors tables.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (default data-proxy)."
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
