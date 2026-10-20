{#
{
  "kind": "template",
  "description": "Create access_policy and access_log tables with a change trigger, row policies that ask for the read_policy, create_policy, edit_policy and delete_policy permissions in the schemas of the token (the log takes only the read_policy policy), a commit that waits for the standbys, and a trigger that moves the counter in rbac.revision on every write.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "user_role": "Database role that every token switches to.",
    "scope": "SQL predicate limiting access to the current schema.",
    "app_schema": "PostgreSQL schema that holds internal functions."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ app_schema }};

CREATE TABLE IF NOT EXISTS {{ schema }}.access_policy (
    subject text NOT NULL,
    unit_type text,
    unit_id text,
    UNIQUE (subject, unit_type, unit_id)
);
ALTER TABLE {{ schema }}.access_policy ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA {{ schema }} TO {{ user_role }};
GRANT SELECT, INSERT, UPDATE, DELETE ON {{ schema }}.access_policy TO {{ user_role }};

CREATE TABLE IF NOT EXISTS {{ schema }}.access_log (
    subject text NOT NULL,
    unit_type text,
    unit_id text,
    action text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS access_log_changed_at
    ON {{ schema }}.access_log (changed_at);
ALTER TABLE {{ schema }}.access_log ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON {{ schema }}.access_log TO {{ user_role }};

CREATE OR REPLACE FUNCTION {{ app_schema }}.log_access_policy_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        EXECUTE format(
            'INSERT INTO %I.access_log (subject, unit_type, unit_id, action) VALUES ($1, $2, $3, $4)',
            TG_TABLE_SCHEMA
        ) USING OLD.subject, OLD.unit_type, OLD.unit_id, 'delete';
    ELSIF TG_OP = 'UPDATE' THEN
        EXECUTE format(
            'INSERT INTO %I.access_log (subject, unit_type, unit_id, action) VALUES ($1, $2, $3, $4)',
            TG_TABLE_SCHEMA
        ) USING OLD.subject, OLD.unit_type, OLD.unit_id, 'update';
    ELSIF TG_OP = 'INSERT' THEN
        EXECUTE format(
            'INSERT INTO %I.access_log (subject, unit_type, unit_id, action) VALUES ($1, $2, $3, $4)',
            TG_TABLE_SCHEMA
        ) USING NEW.subject, NEW.unit_type, NEW.unit_id, 'insert';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;
DROP TRIGGER IF EXISTS access_log_trigger ON {{ schema }}.access_policy;
CREATE TRIGGER access_log_trigger
AFTER INSERT OR UPDATE OR DELETE ON {{ schema }}.access_policy
FOR EACH ROW EXECUTE FUNCTION {{ app_schema }}.log_access_policy_change();

DROP POLICY IF EXISTS user_read ON {{ schema }}.access_log;
CREATE POLICY user_read ON {{ schema }}.access_log
FOR SELECT TO {{ user_role }}
USING ({{ scope }} AND rbac.can('read_policy'));

DROP POLICY IF EXISTS user_read ON {{ schema }}.access_policy;
CREATE POLICY user_read ON {{ schema }}.access_policy
FOR SELECT TO {{ user_role }}
USING ({{ scope }} AND rbac.can('read_policy'));

DROP POLICY IF EXISTS user_insert ON {{ schema }}.access_policy;
CREATE POLICY user_insert ON {{ schema }}.access_policy
FOR INSERT TO {{ user_role }}
WITH CHECK ({{ scope }} AND rbac.can('create_policy'));

DROP POLICY IF EXISTS user_update ON {{ schema }}.access_policy;
CREATE POLICY user_update ON {{ schema }}.access_policy
FOR UPDATE TO {{ user_role }}
USING ({{ scope }} AND rbac.can('edit_policy'))
WITH CHECK ({{ scope }} AND rbac.can('edit_policy'));

DROP POLICY IF EXISTS user_delete ON {{ schema }}.access_policy;
CREATE POLICY user_delete ON {{ schema }}.access_policy
FOR DELETE TO {{ user_role }}
USING ({{ scope }} AND rbac.can('delete_policy'));

CREATE OR REPLACE FUNCTION {{ app_schema }}.require_remote_apply()
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
FOR EACH STATEMENT EXECUTE FUNCTION {{ app_schema }}.require_remote_apply();

CREATE OR REPLACE FUNCTION {{ app_schema }}.bump_revision()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    UPDATE rbac.revision SET value = value + 1;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS access_policy_revision_trigger ON {{ schema }}.access_policy;
CREATE TRIGGER access_policy_revision_trigger
AFTER INSERT OR UPDATE OR DELETE ON {{ schema }}.access_policy
FOR EACH STATEMENT EXECUTE FUNCTION {{ app_schema }}.bump_revision()
