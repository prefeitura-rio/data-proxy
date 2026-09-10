SELECT schema_name FROM information_schema.schemata
WHERE schema_name IN ('${schema}', '${other_schema}') ORDER BY schema_name
