-- +goose Up
-- noqa: disable=PRS,CP02,LT05
-- Adds data_proxy.meta, one row for each served table with the facts that the generic
-- functions agg(), hll() and changes() read when they run: the columns, the row rules, the
-- claim, the scope and the DuckLake location. The sync writes the row with the view. The
-- migration drops the per-table functions `<table>_agg` and `<table>_dl_chg` of the data
-- schemas, which those generic functions replace, and the cleanup procedure deletes the row
-- of a table that leaves the configuration. The Down part restores the procedure and leaves
-- the drop alone, because the sync of the old release creates the functions again.
CREATE TABLE data_proxy.meta (
    schema_name text NOT NULL,
    table_name text NOT NULL,
    source_table text NOT NULL,
    scope_path text NOT NULL,
    claim_setting text NOT NULL,
    data_path text NOT NULL,
    view text NOT NULL,
    max_rows bigint NOT NULL,
    columns jsonb NOT NULL,
    rls_mappings jsonb NOT NULL,
    fallbacks jsonb NOT NULL,
    PRIMARY KEY (schema_name, table_name)
);

REVOKE ALL ON data_proxy.meta FROM PUBLIC;

-- +goose StatementBegin
DO $drop$
DECLARE
    v_function record;
BEGIN
    FOR v_function IN
        SELECT p.oid::regprocedure::text AS signature
        FROM pg_catalog.pg_proc AS p
        INNER JOIN pg_catalog.pg_namespace AS n ON p.pronamespace = n.oid
        INNER JOIN console.schemas AS s ON n.nspname = s.name
        WHERE
            (p.proname LIKE '%\_agg' AND p.pronargs = 0)
            OR (p.proname LIKE '%\_dl\_chg' AND p.pronargs = 2)
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
            IF octet_length(target_table || '_dl_fn') <= 63 THEN
                EXECUTE format('DROP FUNCTION IF EXISTS %I.%I()', target_schema, target_table || '_dl_fn');
            END IF;
            DELETE FROM data_proxy.meta
            WHERE schema_name = target_schema AND table_name = target_table;
        END LOOP;
    END LOOP;
END;
$$;
-- +goose StatementEnd

-- +goose Down
-- noqa: disable=PRS,CP02,LT05
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

DROP TABLE data_proxy.meta;
