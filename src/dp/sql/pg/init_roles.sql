DO $$
BEGIN
    CREATE ROLE ${user_role} NOLOGIN NOBYPASSRLS;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
GRANT ${user_role} TO ${authenticator_role}
