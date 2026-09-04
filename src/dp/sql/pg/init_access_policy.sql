CREATE TABLE IF NOT EXISTS ${schema}.access_policy (
    subject text NOT NULL,
    is_admin boolean NOT NULL DEFAULT false,
    is_enabled boolean NOT NULL DEFAULT true,
    unit_type text,
    unit_id text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (subject, unit_type, unit_id)
);

ALTER TABLE ${schema}.access_policy ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION ${schema}.set_access_policy_metadata_timestamps()
RETURNS trigger AS $$$$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.metadata := coalesce(NEW.metadata, '{}'::jsonb)
            || jsonb_build_object('created_at', now(), 'updated_at', now());
    ELSE
        NEW.metadata := coalesce(NEW.metadata, '{}'::jsonb)
            || jsonb_build_object(
                'created_at', coalesce(OLD.metadata->'created_at', to_jsonb(now())),
                'updated_at', now()
            );
    END IF;
    RETURN NEW;
END;
$$$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS access_policy_metadata_timestamps ON ${schema}.access_policy;
CREATE TRIGGER access_policy_metadata_timestamps
BEFORE INSERT OR UPDATE ON ${schema}.access_policy
FOR EACH ROW EXECUTE FUNCTION ${schema}.set_access_policy_metadata_timestamps();

GRANT SELECT ON ${schema}.access_policy TO ${user_role};

DROP POLICY IF EXISTS user_read ON ${schema}.access_policy;
CREATE POLICY user_read ON ${schema}.access_policy
FOR SELECT
TO ${user_role}
USING (${scope});
