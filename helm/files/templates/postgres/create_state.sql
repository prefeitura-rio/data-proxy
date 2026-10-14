{#
{
  "kind": "template",
  "description": "Create the application schema and the tables where the engine keeps the state of each synced table and its errors.",
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
)
