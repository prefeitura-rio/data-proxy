-- +goose Up
-- noqa: disable=PRS

-- Creates console.enqueue_once() and the triggers reload_proxy, provision_schemas,
-- cleanup_tables and reconcile_schedules. A change to the configuration, the access
-- policy or rbac.revision enqueues a reload, and a change to a schema enqueues
-- provisioning. A workflow queues only when none of its kind waits, so a burst of
-- changes shares one run.
-- noqa: disable=CP02
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.enqueue_once() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM dbos.workflow_status
        WHERE name = TG_ARGV[0] AND queue_name = TG_ARGV[1] AND status = 'ENQUEUED'
    ) THEN
        PERFORM dbos.enqueue_workflow(
            workflow_name => TG_ARGV[0], queue_name => TG_ARGV[1]
        );
    END IF;
    RETURN NULL;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.enqueue_once() FROM PUBLIC;

CREATE OR REPLACE TRIGGER reload_proxy
AFTER INSERT OR UPDATE OR DELETE ON console.schemas
FOR EACH STATEMENT EXECUTE FUNCTION console.enqueue_once('reload_proxy', 'proxy');

CREATE OR REPLACE TRIGGER reload_proxy
AFTER INSERT OR UPDATE OR DELETE ON console.tables
FOR EACH STATEMENT EXECUTE FUNCTION console.enqueue_once('reload_proxy', 'proxy');

CREATE OR REPLACE TRIGGER reload_proxy
AFTER INSERT OR UPDATE OR DELETE ON console.rls
FOR EACH STATEMENT EXECUTE FUNCTION console.enqueue_once('reload_proxy', 'proxy');

CREATE OR REPLACE TRIGGER reload_proxy
AFTER UPDATE ON rbac.revision
FOR EACH STATEMENT EXECUTE FUNCTION console.enqueue_once('reload_proxy', 'proxy');

CREATE OR REPLACE TRIGGER provision_schemas
AFTER INSERT OR UPDATE OR DELETE ON console.schemas
FOR EACH STATEMENT
EXECUTE FUNCTION console.enqueue_once('provision_schemas', 'provision');

CREATE OR REPLACE TRIGGER cleanup_tables
AFTER DELETE ON console.tables
FOR EACH STATEMENT
EXECUTE FUNCTION console.enqueue_once('cleanup_tables', 'provision');

CREATE OR REPLACE TRIGGER reconcile_schedules
AFTER INSERT OR DELETE ON console.schemas
FOR EACH STATEMENT
EXECUTE FUNCTION console.enqueue_once('reconcile_schedules', 'schedules');
