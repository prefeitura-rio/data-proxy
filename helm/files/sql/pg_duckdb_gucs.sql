ALTER SYSTEM SET duckdb.allow_unsigned_extensions = 'on';
ALTER SYSTEM SET duckdb.allow_community_extensions = 'on';
ALTER SYSTEM SET duckdb.unsafe_allow_execution_inside_functions = 'on';
ALTER SYSTEM SET duckdb.postgres_role = 'authenticator';
SELECT pg_reload_conf();
