DO $$$$
BEGIN
    CREATE ROLE ${policy_writer_role} NOLOGIN NOBYPASSRLS;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$$$;
GRANT ${policy_writer_role} TO ${authenticator_role};
GRANT USAGE ON SCHEMA rls TO ${policy_writer_role};
GRANT USAGE ON SCHEMA ${schema} TO ${policy_writer_role};
GRANT SELECT, INSERT, UPDATE ON ${schema}.access_policy TO ${policy_writer_role};

DROP POLICY IF EXISTS ${policy_name} ON ${schema}.access_policy;
CREATE POLICY ${policy_name} ON ${schema}.access_policy
FOR ALL
TO ${policy_writer_role}
USING (true)
WITH CHECK (true)
