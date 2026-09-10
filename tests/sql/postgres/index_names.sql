SELECT indexname FROM pg_indexes
WHERE schemaname = '${schema}' AND tablename = '${table}';
