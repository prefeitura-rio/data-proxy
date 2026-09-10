SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = '${schema}' AND table_name = '${table}'
ORDER BY column_name
