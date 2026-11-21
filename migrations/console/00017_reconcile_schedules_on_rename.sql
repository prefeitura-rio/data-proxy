-- +goose Up
-- noqa: disable=PRS
-- Makes the trigger reconcile_schedules also fire on an update of the name, the
-- project or the short name of a schema, so that a rename deletes the schedule of the
-- old name and creates the one of the new name. The trigger keep_name refuses a
-- rename today. The Down part restores the trigger of 00012.
CREATE OR REPLACE TRIGGER reconcile_schedules
AFTER INSERT OR UPDATE OF name, project, short_name OR DELETE ON console.schemas
FOR EACH STATEMENT
EXECUTE FUNCTION console.enqueue_once('reconcile_schedules', 'schedules');

-- +goose Down
-- noqa: disable=PRS
CREATE OR REPLACE TRIGGER reconcile_schedules
AFTER INSERT OR DELETE ON console.schemas
FOR EACH STATEMENT
EXECUTE FUNCTION console.enqueue_once('reconcile_schedules', 'schedules');
