{#
{
  "kind": "template",
  "description": "Move DBOS application state from the legacy dp schema.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state."
  }
}
#}
DO $$
BEGIN
    IF {{ schema }} <> 'dp'
       AND EXISTS (SELECT FROM pg_namespace WHERE nspname = 'dp') THEN
        INSERT INTO {{ schema }}.state (table_name, state)
        SELECT table_name, state
        FROM dp.state
        ON CONFLICT (table_name) DO UPDATE
        SET state = EXCLUDED.state;

        INSERT INTO {{ schema }}.errors (reason, fields, recorded_at)
        SELECT reason, fields, recorded_at
        FROM dp.errors;

        DROP SCHEMA dp CASCADE;
    END IF;
END;
$$;
