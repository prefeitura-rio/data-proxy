DO $$$$
BEGIN
    CREATE ROLE ${policy_writer_role} NOLOGIN NOBYPASSRLS;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$$$;
GRANT ${policy_writer_role} TO ${authenticator_role};
GRANT USAGE ON SCHEMA rls TO ${policy_writer_role};
GRANT SELECT, INSERT, UPDATE, DELETE ON rls.access_policy TO ${policy_writer_role};

DROP POLICY IF EXISTS ${policy_name} ON rls.access_policy;
CREATE POLICY ${policy_name} ON rls.access_policy
FOR ALL
TO ${policy_writer_role}
USING (schema = ${schema_literal})
WITH CHECK (schema = ${schema_literal})
