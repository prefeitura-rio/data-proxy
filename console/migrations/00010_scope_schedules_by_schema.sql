-- +goose Up
-- noqa: disable=CP02,PRS
-- The one place in SQL that knows the prefix of the schedule of a schema.
-- +goose StatementBegin
CREATE FUNCTION console.schedule_schema(schedule_name text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN starts_with(schedule_name, 'sync-') AND length(schedule_name) > 5
            THEN substr(schedule_name, 6)
    END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.schedule_schema(text) FROM PUBLIC;

CREATE OR REPLACE VIEW console.schedules AS
SELECT
    schedule_name AS name,
    workflow_name,
    schedule,
    status,
    queue_name,
    cron_timezone AS timezone,
    automatic_backfill,
    last_fired_at,
    console.schedule_schema(schedule_name) AS schema
FROM dbos.workflow_schedules
WHERE
    console.schedule_schema(schedule_name) IS NOT NULL
    AND rbac.can('read_sync')
    AND rbac.mine(console.schedule_schema(schedule_name));

-- +goose Down
DROP VIEW console.schedules;
DROP FUNCTION console.schedule_schema(text);

CREATE VIEW console.schedules AS
SELECT
    schedule_name AS name,
    workflow_name,
    schedule,
    status,
    queue_name,
    cron_timezone AS timezone,
    automatic_backfill,
    last_fired_at
FROM dbos.workflow_schedules
WHERE rbac.can('read_sync');
