{#
{
  "kind": "template",
  "description": "Upsert committed state for one table into data-proxy.state.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (set by DBOS_APP_SCHEMA)."
  }
}
#}
INSERT INTO {{ schema }}.state (table_name, state)
VALUES (%(table_name)s, %(state)s::jsonb)
ON CONFLICT (table_name) DO UPDATE
SET state = EXCLUDED.state;
