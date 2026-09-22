{#
{
  "kind": "template",
  "description": "Delete access-log rows older than the configured retention interval.",
  "inputs": {
    "schema": "PostgreSQL schema for the procedure."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE OR REPLACE PROCEDURE {{ schema }}.prune_access_log(
    p_retention interval,
    p_schema_name text DEFAULT NULL
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp
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
REVOKE ALL ON PROCEDURE {{ schema }}.prune_access_log(interval, text) FROM public;

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'jobs') THEN
        GRANT USAGE ON SCHEMA {{ schema }} TO jobs;
        GRANT EXECUTE ON PROCEDURE {{ schema }}.apply_retention(jsonb, text) TO jobs;
        GRANT EXECUTE ON PROCEDURE {{ schema }}.prune_access_log(interval, text) TO jobs;
    END IF;
END;
$$;
