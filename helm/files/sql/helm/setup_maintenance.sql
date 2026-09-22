{#
{
  "kind": "template",
  "description": "Create all data_proxy maintenance procedures for application database initialization.",
  "inputs": {
    "schema": "PostgreSQL schema for maintenance procedures."
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
    changed boolean := false;
    schema_changed boolean;
BEGIN
    FOR target_schema IN
        SELECT name
        FROM jsonb_each(p_config -> 'schemas') AS config_schema(name, value)
        WHERE p_schema_name IS NULL OR config_schema.name = p_schema_name
    LOOP
        schema_changed := false;
        FOR target_table IN
            SELECT tables.tablename
            FROM pg_tables AS tables
            WHERE tables.schemaname = target_schema
              AND tables.tablename NOT IN ('freshness', 'access_policy', 'access_log')
              AND NOT EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      p_config -> 'schemas' -> target_schema -> 'tables'
                  ) AS config_table(value)
                  WHERE split_part(config_table.value ->> 'name', '.', 3) = tables.tablename
              )
        LOOP
            EXECUTE format('DELETE FROM %I.freshness WHERE "table" = $1', target_schema) USING target_table;
            EXECUTE format('DROP TABLE IF EXISTS %I.%I CASCADE', target_schema, target_table);
            changed := true;
            schema_changed := true;
        END LOOP;

        FOR target_table IN
            SELECT DISTINCT left(class.relname, -3)
            FROM pg_class AS class
            INNER JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = target_schema
              AND class.relkind = 'v'
              AND right(class.relname, 3) = '_bq'
              AND NOT EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      p_config -> 'schemas' -> target_schema -> 'tables'
                  ) AS config_table(value)
                  WHERE config_table.value ->> 'fallback' = 'true'
                    AND split_part(config_table.value ->> 'name', '.', 3) = left(class.relname, -3)
              )
            UNION
            SELECT DISTINCT left(procedure.proname, -6)
            FROM pg_proc AS procedure
            INNER JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = target_schema
              AND right(procedure.proname, 6) = '_bq_fn'
              AND NOT EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      p_config -> 'schemas' -> target_schema -> 'tables'
                  ) AS config_table(value)
                  WHERE config_table.value ->> 'fallback' = 'true'
                    AND split_part(config_table.value ->> 'name', '.', 3) = left(procedure.proname, -6)
              )
        LOOP
            EXECUTE format('DROP VIEW IF EXISTS %I.%I', target_schema, target_table || '_bq');
            EXECUTE format('DROP FUNCTION IF EXISTS %I.%I()', target_schema, target_table || '_bq_fn');
            changed := true;
            schema_changed := true;
        END LOOP;

        IF schema_changed THEN
            EXECUTE format('DELETE FROM %I.access_policy', target_schema);
        END IF;
    END LOOP;

    IF changed THEN
        PERFORM pg_notify('pgrst', 'reload schema');
    END IF;
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

CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE OR REPLACE PROCEDURE {{ schema }}.prune_access_log(
    p_retention interval,
    p_schema_name text DEFAULT NULL
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp
AS $$
DECLARE
    target_schema text;
BEGIN
    IF p_schema_name IS NULL THEN
        RAISE EXCEPTION 'schema name is required';
    END IF;

    target_schema := p_schema_name;
    EXECUTE format(
        'DELETE FROM %I.access_log WHERE changed_at < now() - $1',
        target_schema
    ) USING p_retention;
END;
$$;
REVOKE ALL ON PROCEDURE {{ schema }}.prune_access_log(interval, text) FROM public;

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'jobs') THEN
        GRANT USAGE ON SCHEMA {{ schema }} TO jobs;
        GRANT EXECUTE ON PROCEDURE {{ schema }}.apply_retention(jsonb, text) TO jobs;
        GRANT EXECUTE ON PROCEDURE {{ schema }}.prune_access_log(interval, text) TO jobs;
    END IF;
END;
$$;
