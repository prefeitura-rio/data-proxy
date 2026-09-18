-- {
--   "kind": "template",
--   "description": "Bootstrap the PostgreSQL roles required by Data Proxy.",
-- }
DO '
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ''dataproxy'') THEN
    CREATE ROLE dataproxy SUPERUSER LOGIN;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ''anon'') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ''user'') THEN
    CREATE ROLE "user" NOLOGIN;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ''authenticator'') THEN
    CREATE ROLE authenticator LOGIN NOINHERIT;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ''backup'') THEN
    CREATE ROLE backup LOGIN;
  END IF;

  GRANT anon TO authenticator;
  GRANT "user" TO authenticator;
END
';
