{#
{
  "kind": "template",
  "description": "Delete rows outside configured table retention windows.",
  "inputs": {
    "schema": "PostgreSQL schema for the procedure."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE OR REPLACE PROCEDURE {{ schema }}.apply_retention(
    p_config jsonb,
    p_schema_name text DEFAULT NULL
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp
AS $$
DECLARE
    target_schema text;
    target_table text;
    target_column text;
    target_window interval;
BEGIN
    FOR target_schema, target_table, target_column, target_window IN
        SELECT
            config_schema.name,
            split_part(config_table.value ->> 'name', '.', 3),
            config_table.value -> 'retention' ->> 'column',
            (config_table.value -> 'retention' ->> 'window')::interval
        FROM jsonb_each(p_config -> 'schemas') AS config_schema(name, value)
        CROSS JOIN LATERAL jsonb_array_elements(config_schema.value -> 'tables') AS config_table(value)
        WHERE (p_schema_name IS NULL OR config_schema.name = p_schema_name)
          AND config_table.value ? 'retention'
    LOOP
        EXECUTE format(
            'DELETE FROM %I.%I WHERE %I < now() - $1',
            target_schema,
            target_table,
            target_column
        ) USING target_window;
    END LOOP;
END;
$$;
REVOKE ALL ON PROCEDURE {{ schema }}.apply_retention(jsonb, text) FROM public;

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'jobs') THEN
        GRANT USAGE ON SCHEMA {{ schema }} TO jobs;
        GRANT EXECUTE ON PROCEDURE {{ schema }}.apply_retention(jsonb, text) TO jobs;
    END IF;
END;
$$;
