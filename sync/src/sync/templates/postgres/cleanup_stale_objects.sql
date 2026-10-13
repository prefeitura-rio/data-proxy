{#
{
  "kind": "template",
  "description": "Remove table views and functions absent from the current synchronization configuration.",
  "inputs": {
    "schema": "PostgreSQL schema for the procedure."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE OR REPLACE PROCEDURE {{ schema }}.cleanup_stale_objects(
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
BEGIN
    FOR target_schema IN
        SELECT name
        FROM jsonb_each(p_config -> 'schemas') AS config_schema(name, value)
        WHERE p_schema_name IS NULL OR config_schema.name = p_schema_name
    LOOP
        FOR target_table IN
            SELECT views.viewname
            FROM pg_views AS views
            WHERE views.schemaname = target_schema
              AND views.viewname NOT IN ('access_policy', 'access_log')
              AND NOT EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      p_config -> 'schemas' -> target_schema -> 'tables'
                  ) AS config_table(value)
                  WHERE split_part(config_table.value ->> 'name', '.', 3) = views.viewname
              )
        LOOP
            EXECUTE format('DROP VIEW IF EXISTS %I.%I CASCADE', target_schema, target_table);
            EXECUTE format('DROP FUNCTION IF EXISTS %I.%I()', target_schema, target_table || '_fn');
        END LOOP;
    END LOOP;
END;
$$;
REVOKE ALL ON PROCEDURE {{ schema }}.cleanup_stale_objects(jsonb, text) FROM public;
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'jobs') THEN
        GRANT USAGE ON SCHEMA {{ schema }} TO jobs;
        GRANT EXECUTE ON PROCEDURE {{ schema }}.cleanup_stale_objects(jsonb, text) TO jobs;
    END IF;
END;
$$;
