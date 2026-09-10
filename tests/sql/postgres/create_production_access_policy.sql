CREATE TABLE ${schema}.access_policy (
    subject text NOT NULL,
    is_admin boolean NOT NULL DEFAULT false,
    is_enabled boolean NOT NULL DEFAULT true,
    unit_type text,
    unit_id text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (subject, unit_type, unit_id)
);
ALTER TABLE ${schema}.access_policy ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON ${schema}.access_policy TO "user";
CREATE POLICY user_read ON ${schema}.access_policy
FOR SELECT TO "user"
USING ('${schema}' = ANY(string_to_array(current_setting('app.claim_schemas', true), ',')));
