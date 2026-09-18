DO '
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ''cnpg_pooler_pgbouncer'') THEN
    CREATE ROLE cnpg_pooler_pgbouncer LOGIN;
  END IF;
END
';
ALTER ROLE cnpg_pooler_pgbouncer
LOGIN
PASSWORD '__POOLER_PASSWORD__';
GRANT CONNECT ON DATABASE postgres TO cnpg_pooler_pgbouncer;
CREATE OR REPLACE FUNCTION public.user_search(uname TEXT)
RETURNS TABLE (usename TEXT, passwd TEXT)
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS
'SELECT usename::text, passwd FROM pg_catalog.pg_shadow WHERE usename=$1;';
REVOKE ALL ON FUNCTION public.user_search(TEXT) FROM public;
GRANT EXECUTE ON FUNCTION public.user_search(TEXT) TO cnpg_pooler_pgbouncer;
