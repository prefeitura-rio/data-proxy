{#
{
  "kind": "template",
  "description": "Make the changes that the proxy and the schemas depend on enqueue a workflow. A change to the sync configuration tables of the console, to the access policy, or to the rules (each moves the counter in rbac.revision) enqueues a reload of the proxy. A change to console.schemas also enqueues the provision of the schemas. A change queues a workflow only when none of that kind waits in its queue. A change that arrives while one runs queues the next run, because the running one may have read the data already, and a burst of changes shares one waiting run. Run it after the rbac schema and the console tables exist.",
  "inputs": {}
}
#}
-- noqa: disable=CP02,PRS
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
