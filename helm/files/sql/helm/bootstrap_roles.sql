-- {
--   "kind": "template",
--   "description": "Bootstrap the PostgreSQL roles required by PostgREST.",
--   "inputs": {
--     "anon_role": "SQL-safe anonymous PostgreSQL role identifier.",
--     "user_role": "SQL-safe authenticated PostgreSQL role identifier.",
--     "authenticator_role": "SQL-safe PostgREST login role identifier.",
--     "authenticator_password": "Password for the PostgREST authenticator role."
--   }
-- }
DO $$
DECLARE
  anon_role text := '{{ replace "'" "''" .Values.auth.anonRole }}';
  user_role text := '{{ replace "'" "''" .Values.auth.userRole }}';
  authenticator_role text := '{{ replace "'" "''" .Values.auth.authenticatorRole }}';
  authenticator_password text := '{{ replace "'" "''" .Values.cnpg.authenticatorPassword }}';
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = anon_role) THEN
    EXECUTE format('CREATE ROLE %I NOLOGIN', anon_role);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_role) THEN
    EXECUTE format('CREATE ROLE %I NOLOGIN', user_role);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = authenticator_role) THEN
    EXECUTE format('CREATE ROLE %I LOGIN NOINHERIT', authenticator_role);
  END IF;

  EXECUTE format('ALTER ROLE %I LOGIN NOINHERIT PASSWORD %L', authenticator_role, authenticator_password);
  EXECUTE format('GRANT %I TO %I', anon_role, authenticator_role);
  EXECUTE format('GRANT %I TO %I', user_role, authenticator_role);
END
$$;
