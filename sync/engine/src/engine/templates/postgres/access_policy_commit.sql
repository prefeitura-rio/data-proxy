{#
{
  "kind": "template",
  "description": "Make every access_policy write wait until the standbys apply it, so a revoked grant never stays readable on a standby.",
  "inputs": {
    "schema": "PostgreSQL schema that owns access_policy.",
    "app_schema": "PostgreSQL schema that holds internal functions."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ app_schema }};

CREATE OR REPLACE FUNCTION {{ app_schema }}.wait_for_standbys()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM set_config('synchronous_commit', 'remote_apply', true);
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS access_policy_commit_trigger ON {{ schema }}.access_policy;
CREATE TRIGGER access_policy_commit_trigger
AFTER INSERT OR UPDATE OR DELETE ON {{ schema }}.access_policy
FOR EACH STATEMENT EXECUTE FUNCTION {{ app_schema }}.wait_for_standbys()
