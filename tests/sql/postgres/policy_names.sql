SELECT policyname FROM pg_policies
WHERE schemaname = '${schema}' AND tablename = '${table}';
