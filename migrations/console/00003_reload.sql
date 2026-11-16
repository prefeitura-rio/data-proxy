-- +goose Up
-- Creates data_proxy.wait_for_standbys(), which waits until every streaming standby
-- replays the current WAL position, and data_proxy.reload_postgrest(), which waits and
-- then sends NOTIFY pgrst.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION data_proxy.wait_for_standbys(
    patience interval DEFAULT interval '15 seconds'
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    target pg_lsn := pg_current_wal_lsn();
    deadline timestamptz := clock_timestamp() + patience;
BEGIN
    WHILE clock_timestamp() < deadline AND EXISTS (
        SELECT FROM pg_stat_replication WHERE state = 'streaming' AND replay_lsn < target
    ) LOOP
        PERFORM pg_sleep(0.05);
    END LOOP;

    IF EXISTS (SELECT FROM pg_stat_replication WHERE state = 'streaming' AND replay_lsn < target) THEN
        RAISE WARNING 'A standby did not replay up to % within %', target, patience;
    END IF;
END;
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION data_proxy.reload_postgrest()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM data_proxy.wait_for_standbys();
    PERFORM pg_notify('pgrst', 'reload schema');
END;
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION data_proxy.wait_for_standbys(interval) FROM public;

REVOKE ALL ON FUNCTION data_proxy.reload_postgrest() FROM public;
