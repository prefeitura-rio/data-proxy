{#
{
  "kind": "template",
  "description": "Remove DBOS table state absent from the current synchronization configuration.",
  "inputs": {
    "schema": "PostgreSQL schema for the procedure."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE OR REPLACE PROCEDURE {{ schema }}.cleanup_table_state(
    p_config jsonb
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp
AS $$
BEGIN
    DELETE FROM {{ schema }}.state AS state
    WHERE NOT EXISTS (
        SELECT 1
        FROM jsonb_each(p_config -> 'schemas') AS config_schema(name, value)
        CROSS JOIN LATERAL jsonb_array_elements(config_schema.value -> 'tables') AS config_table(value)
        WHERE config_table.value ->> 'name' = state.table_name
    );
END;
$$;
REVOKE ALL ON PROCEDURE {{ schema }}.cleanup_table_state(jsonb) FROM public;
