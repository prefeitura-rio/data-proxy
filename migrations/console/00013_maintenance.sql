-- +goose Up
-- noqa: disable=PRS

-- Creates three SECURITY DEFINER procedures in data_proxy: cleanup_stale_objects()
-- drops views and functions of tables that left the sync configuration,
-- prune_access_log() deletes old access-log rows, and prune_workflows() deletes
-- finished DBOS workflows, old errors and orphan scope rows.
CREATE SCHEMA IF NOT EXISTS data_proxy;

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

REVOKE ALL ON PROCEDURE data_proxy.cleanup_stale_objects(jsonb, text) FROM public;


-- Delete access-log rows older than the configured retention interval.
CREATE SCHEMA IF NOT EXISTS data_proxy;

-- +goose StatementBegin
CREATE OR REPLACE PROCEDURE data_proxy.prune_access_log(
    p_retention interval,
    p_schema_name text DEFAULT NULL
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, data_proxy, pg_temp
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
-- +goose StatementEnd

REVOKE ALL ON PROCEDURE data_proxy.prune_access_log(interval, text) FROM public;


-- Create a procedure that deletes the DBOS workflows that finished before the
-- retention interval, the error rows that are older than it, and the scope rows of the
-- workflows that no longer exist.
CREATE SCHEMA IF NOT EXISTS data_proxy;

-- +goose StatementBegin
CREATE OR REPLACE PROCEDURE data_proxy.prune_workflows(p_retention interval)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, dbos, pg_temp
AS $$
DECLARE
    pruned_count integer;
BEGIN
    DELETE FROM dbos.workflow_status AS workflow
    WHERE workflow.status IN ('SUCCESS', 'ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED')
      AND workflow.updated_at < (
          EXTRACT(epoch FROM clock_timestamp() - p_retention) * 1000
      )::bigint;

    GET DIAGNOSTICS pruned_count = ROW_COUNT;

    DELETE FROM data_proxy.errors
    WHERE recorded_at < clock_timestamp() - p_retention;

    DELETE FROM data_proxy.scope AS scope
    WHERE NOT EXISTS (
        SELECT 1
        FROM dbos.workflow_status AS workflow
        WHERE workflow.workflow_uuid = scope.workflow_id
    );

    RAISE NOTICE 'Pruned % DBOS workflows', pruned_count;
END;
$$;
-- +goose StatementEnd

REVOKE ALL ON PROCEDURE data_proxy.prune_workflows(interval) FROM public;
