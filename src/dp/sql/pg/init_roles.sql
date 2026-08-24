DO $$$$
BEGIN
    CREATE ROLE ${user_role} NOLOGIN NOBYPASSRLS;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$$$;
GRANT ${user_role} TO ${authenticator_role};
GRANT USAGE ON SCHEMA ${rls_schema} TO ${user_role};
DO $$$$
BEGIN
    CREATE TYPE ${rls_schema}.sync_status AS ENUM ('success', 'failure');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$$$
