{#
{
  "kind": "template",
  "description": "Create the functions that tell PostgREST and the proxy to read the database again, after every standby replayed the change.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema that holds application state."
  }
}
#}
CREATE OR REPLACE FUNCTION {{ schema }}.wait_for_standbys(
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

CREATE OR REPLACE FUNCTION {{ schema }}.reload_postgrest()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM {{ schema }}.wait_for_standbys();
    PERFORM pg_notify('pgrst', 'reload schema');
END;
$$;

REVOKE ALL ON FUNCTION {{ schema }}.wait_for_standbys(interval) FROM public;
REVOKE ALL ON FUNCTION {{ schema }}.reload_postgrest() FROM public;
