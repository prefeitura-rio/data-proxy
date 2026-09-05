ALTER TABLE ${schema}.${table} ALTER COLUMN ${column} SET DATA TYPE jsonb USING ${column}::jsonb
