-- +goose Up
-- noqa: disable=CP02,PRS
-- +goose StatementBegin
CREATE FUNCTION admin.config() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    WITH members AS (
        SELECT
            schema,
            jsonb_agg(
                jsonb_strip_nulls(jsonb_build_object('name', name, 'cache_ttl', cache_ttl))
                ORDER BY name
            ) AS tables
        FROM admin.tables
        GROUP BY schema
    )

    SELECT jsonb_build_object(
        'schemas',
        coalesce(
            jsonb_object_agg(
                s.name, jsonb_build_object('tables', coalesce(m.tables, '[]'::jsonb))
            ),
            '{}'::jsonb
        )
    )
    FROM admin.schemas AS s
    LEFT JOIN members AS m ON s.name = m.schema
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION admin.config() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.config() TO console_viewer;

-- +goose StatementBegin
CREATE FUNCTION admin.reload_proxy() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    BEGIN
        PERFORM dbos.enqueue_workflow(
            workflow_name => 'reload_proxy',
            queue_name => 'proxy',
            deduplication_id => 'reload_proxy'
        );
    EXCEPTION
        WHEN unique_violation THEN NULL;
    END;
    RETURN NULL;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION admin.reload_proxy() FROM PUBLIC;

CREATE TRIGGER reload_proxy AFTER INSERT OR UPDATE OR DELETE ON admin.schemas
FOR EACH STATEMENT EXECUTE FUNCTION admin.reload_proxy();
CREATE TRIGGER reload_proxy AFTER INSERT OR UPDATE OR DELETE ON admin.tables
FOR EACH STATEMENT EXECUTE FUNCTION admin.reload_proxy();
CREATE TRIGGER reload_proxy AFTER INSERT OR UPDATE OR DELETE ON admin.rls
FOR EACH STATEMENT EXECUTE FUNCTION admin.reload_proxy();

-- +goose Down
DROP TRIGGER reload_proxy ON admin.rls;
DROP TRIGGER reload_proxy ON admin.tables;
DROP TRIGGER reload_proxy ON admin.schemas;
DROP FUNCTION admin.reload_proxy();
DROP FUNCTION admin.config();
