DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup') THEN
        CREATE ROLE backup;
    END IF;
END
$$;
ALTER ROLE backup
    NOINHERIT LOGIN NOBYPASSRLS
    PASSWORD :'backup_password'
