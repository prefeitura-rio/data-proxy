-- +goose Up
-- noqa: disable=PRS,CP02
-- Drops the functions of the data schemas that carry the names of an older release,
-- `dl_changes_<table>` and `<table>_aggregate`, and gives the procedure
-- cleanup_stale_objects() the new names `<table>_dl_chg` and `<table>_agg`. The sync
-- creates the functions under the new names. The Down part restores the procedure
-- and leaves the drop alone, because the sync of the old release creates them again.

-- +goose StatementBegin
DO $drop$
DECLARE
    v_function record;
BEGIN
    FOR v_function IN
        SELECT n.nspname AS schema_name, p.proname AS function_name, p.oid::regprocedure::text AS signature
        FROM pg_catalog.pg_proc AS p
        INNER JOIN pg_catalog.pg_namespace AS n ON p.pronamespace = n.oid
        INNER JOIN console.schemas AS s ON n.nspname = s.name
        WHERE
            (p.proname LIKE 'dl\_changes\_%' AND p.pronargs = 2)
            OR (p.proname LIKE '%\_aggregate' AND p.pronargs = 0)
    LOOP
        EXECUTE format('DROP FUNCTION %s', v_function.signature);
    END LOOP;
END
$drop$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE PROCEDURE data_proxy.cleanup_stale_objects(
    p_config jsonb,
    p_schema_name text DEFAULT NULL
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, data_proxy, pg_temp
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
            IF octet_length(target_table || '_agg') <= 63 THEN
                EXECUTE format('DROP FUNCTION IF EXISTS %I.%I()', target_schema, target_table || '_agg');
            END IF;
            IF octet_length(target_table || '_dl_fn') <= 63 THEN
                EXECUTE format('DROP FUNCTION IF EXISTS %I.%I()', target_schema, target_table || '_dl_fn');
            END IF;
            IF octet_length(target_table || '_dl_chg') <= 63 THEN
                EXECUTE format(
                    'DROP FUNCTION IF EXISTS %I.%I(bigint, bigint)', target_schema, target_table || '_dl_chg'
                );
            END IF;
        END LOOP;
    END LOOP;
END;
$$;
-- +goose StatementEnd

-- +goose Down
-- noqa: disable=PRS,CP02
-- +goose StatementBegin
CREATE OR REPLACE PROCEDURE data_proxy.cleanup_stale_objects(
    p_config jsonb,
    p_schema_name text DEFAULT NULL
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, data_proxy, pg_temp
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
            IF octet_length(target_table || '_aggregate') <= 63 THEN
                EXECUTE format('DROP FUNCTION IF EXISTS %I.%I()', target_schema, target_table || '_aggregate');
            END IF;
        END LOOP;
    END LOOP;
END;
$$;
-- +goose StatementEnd
