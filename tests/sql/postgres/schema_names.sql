SELECT schema_name FROM information_schema.schemata
WHERE schema_name IN ('app', 'other') ORDER BY schema_name;
