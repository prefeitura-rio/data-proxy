-- +goose Up
-- noqa: disable=CP02,PRS
-- +goose StatementBegin
CREATE FUNCTION admin.update_queue() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    problem text;
BEGIN
    problem := CASE
        WHEN new.concurrency <= 0 THEN 'concurrency must be positive'
        WHEN new.worker_concurrency <= 0 THEN 'worker_concurrency must be positive'
        WHEN new.worker_concurrency > new.concurrency
            THEN 'worker_concurrency can''t exceed concurrency'
        WHEN new.rate_limit_max <= 0 THEN 'rate_limit_max must be positive'
        WHEN new.rate_limit_period_sec <= 0 THEN 'rate_limit_period_sec must be positive'
        WHEN new.polling_interval_sec <= 0 THEN 'polling_interval_sec must be positive'
    END;

    IF problem IS NOT NULL THEN
        RAISE EXCEPTION '%', problem USING ERRCODE = 'check_violation';
    END IF;

    UPDATE dbos.queues
    SET concurrency = new.concurrency,
        worker_concurrency = new.worker_concurrency,
        rate_limit_max = new.rate_limit_max,
        rate_limit_period_sec = new.rate_limit_period_sec,
        polling_interval_sec = new.polling_interval_sec,
        updated_at = (extract(epoch FROM now()) * 1000)::bigint
    WHERE name = old.name;

    RETURN new;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION admin.update_schedule() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    problem text;
BEGIN
    problem := CASE
        WHEN new.status NOT IN ('ACTIVE', 'PAUSED') THEN 'status must be ACTIVE or PAUSED'
        WHEN new.schedule !~ '^([0-9A-Za-z*/,?#-]+ ){4,5}[0-9A-Za-z*/,?#-]+$'
            THEN 'schedule must be a cron expression with five or six fields'
        WHEN new.timezone IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = new.timezone)
            THEN 'timezone is not a known time zone'
    END;

    IF problem IS NOT NULL THEN
        RAISE EXCEPTION '%', problem USING ERRCODE = 'check_violation';
    END IF;

    UPDATE dbos.workflow_schedules
    SET schedule = new.schedule,
        status = new.status,
        cron_timezone = new.timezone,
        automatic_backfill = new.automatic_backfill
    WHERE schedule_name = old.name;

    RETURN new;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION admin.update_queue() FROM PUBLIC;
REVOKE ALL ON FUNCTION admin.update_schedule() FROM PUBLIC;

CREATE TRIGGER update_queue
INSTEAD OF UPDATE ON admin.queues
FOR EACH ROW EXECUTE FUNCTION admin.update_queue();

CREATE TRIGGER update_schedule
INSTEAD OF UPDATE ON admin.schedules
FOR EACH ROW EXECUTE FUNCTION admin.update_schedule();

GRANT UPDATE (
    concurrency,
    worker_concurrency,
    rate_limit_max,
    rate_limit_period_sec,
    polling_interval_sec
) ON admin.queues TO console_operator;

GRANT UPDATE (schedule, status, timezone, automatic_backfill)
ON admin.schedules TO console_operator;

-- +goose Down
DROP TRIGGER update_schedule ON admin.schedules;
DROP TRIGGER update_queue ON admin.queues;
DROP FUNCTION admin.update_schedule();
DROP FUNCTION admin.update_queue();
