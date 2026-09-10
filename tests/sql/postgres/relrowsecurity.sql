SELECT relrowsecurity FROM pg_class WHERE oid = '${schema}.${table}'::regclass;
