{#
{
  "kind": "template",
  "description": "Create access_policy and access_log tables with a change trigger and a scoped RLS read policy.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "user_role": "Application database role.",
    "scope": "SQL predicate limiting access to the current schema."
  }
}
#}
CREATE TABLE IF NOT EXISTS {{ schema }}.access_policy (
    subject text NOT NULL,
    is_admin boolean NOT NULL DEFAULT false,
    unit_type text,
    unit_id text,
    UNIQUE (subject, unit_type, unit_id) INCLUDE (is_admin)
);
ALTER TABLE {{ schema }}.access_policy ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON {{ schema }}.access_policy TO {{ user_role }};

CREATE TABLE IF NOT EXISTS {{ schema }}.access_log (
    subject text NOT NULL,
    is_admin boolean NOT NULL,
    unit_type text,
    unit_id text,
    action text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS access_log_changed_at
    ON {{ schema }}.access_log (changed_at);

CREATE OR REPLACE FUNCTION {{ schema }}.log_access_policy_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO {{ schema }}.access_log (subject, is_admin, unit_type, unit_id, action)
        VALUES (OLD.subject, OLD.is_admin, OLD.unit_type, OLD.unit_id, 'delete');
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO {{ schema }}.access_log (subject, is_admin, unit_type, unit_id, action)
        VALUES (OLD.subject, OLD.is_admin, OLD.unit_type, OLD.unit_id, 'update');
    ELSIF TG_OP = 'INSERT' THEN
        INSERT INTO {{ schema }}.access_log (subject, is_admin, unit_type, unit_id, action)
        VALUES (NEW.subject, NEW.is_admin, NEW.unit_type, NEW.unit_id, 'insert');
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;
DROP TRIGGER IF EXISTS access_log_trigger ON {{ schema }}.access_policy;
CREATE TRIGGER access_log_trigger
AFTER INSERT OR UPDATE OR DELETE ON {{ schema }}.access_policy
FOR EACH ROW EXECUTE FUNCTION {{ schema }}.log_access_policy_change();

DROP POLICY IF EXISTS user_read ON {{ schema }}.access_policy;
CREATE POLICY user_read ON {{ schema }}.access_policy
FOR SELECT TO {{ user_role }}
USING ({{ scope }})
