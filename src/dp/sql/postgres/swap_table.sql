DROP TABLE IF EXISTS ${schema}.${old_table};
ALTER TABLE IF EXISTS ${schema}.${table} RENAME TO ${old_table};
ALTER TABLE ${schema}.${next_table} RENAME TO ${table};
DROP TABLE IF EXISTS ${schema}.${old_table}
