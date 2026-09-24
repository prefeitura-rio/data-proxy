{#
{
  "kind": "template",
  "description": "Grant the shared jobs role schema access, prune rights, and the SECURITY DEFINER helper that drops stale tables.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage(schema, 'jobs') }}
GRANT SELECT, DELETE ON {{ schema }}.access_policy TO jobs;
DROP POLICY IF EXISTS backup_read ON {{ schema }}.access_policy;
DROP POLICY IF EXISTS jobs_access ON {{ schema }}.access_policy;
CREATE POLICY jobs_access ON {{ schema }}.access_policy
FOR ALL TO jobs
USING (true)
WITH CHECK (true);
GRANT SELECT, DELETE ON {{ schema }}.access_log TO jobs;
GRANT SELECT, DELETE ON {{ schema }}.freshness TO jobs;
DROP POLICY IF EXISTS jobs_access ON {{ schema }}.freshness;
CREATE POLICY jobs_access ON {{ schema }}.freshness
FOR ALL TO jobs
USING (true)
WITH CHECK (true);
CREATE OR REPLACE FUNCTION {{ schema }}.drop_table_if_exists(target_table text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    EXECUTE format('DROP TABLE IF EXISTS {{ schema }}.%I CASCADE', target_table);
END;
$$;
REVOKE ALL ON FUNCTION {{ schema }}.drop_table_if_exists(text) FROM public;
GRANT EXECUTE ON FUNCTION {{ schema }}.drop_table_if_exists(text) TO jobs;
CREATE OR REPLACE FUNCTION {{ schema }}.drop_fallback_objects(target_table text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    EXECUTE format('DROP VIEW IF EXISTS {{ schema }}.%I', target_table || '_bq');
    EXECUTE format('DROP FUNCTION IF EXISTS {{ schema }}.%I()', target_table || '_bq_fn');
END;
$$;
REVOKE ALL ON FUNCTION {{ schema }}.drop_fallback_objects(text) FROM public;
GRANT EXECUTE ON FUNCTION {{ schema }}.drop_fallback_objects(text) TO jobs
