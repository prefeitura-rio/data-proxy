SELECT grantee FROM information_schema.role_table_grants
WHERE table_schema = 'app' AND table_name = 'table'
AND privilege_type = 'SELECT' AND grantee = 'user';
