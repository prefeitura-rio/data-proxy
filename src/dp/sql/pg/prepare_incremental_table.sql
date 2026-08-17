DROP TABLE IF EXISTS ${schema}.${next_table};
CREATE TABLE ${schema}.${next_table} (
    LIKE ${schema}.${table}
    INCLUDING DEFAULTS
    INCLUDING GENERATED
    INCLUDING IDENTITY
    INCLUDING CONSTRAINTS
    INCLUDING STORAGE
    INCLUDING COMMENTS
);
INSERT INTO ${schema}.${next_table}
SELECT * FROM ${schema}.${table}
WHERE NOT (${affected_partitions})
