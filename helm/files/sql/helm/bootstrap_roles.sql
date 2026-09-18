-- {
--   "kind": "template",
--   "description": "Bootstrap the fixed PostgreSQL roles required by PostgREST.",
--   "inputs": {
--     "authenticator_password": "Password for the PostgREST authenticator role."
--   }
-- }
CREATE ROLE anon NOLOGIN;
CREATE ROLE user NOLOGIN;
CREATE ROLE authenticator
LOGIN NOINHERIT
PASSWORD '{{ .Values.cnpg.authenticatorPassword }}';
GRANT anon TO authenticator;
GRANT user TO authenticator;
