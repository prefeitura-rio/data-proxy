CREATE TABLE ${schema}.${table} (LIKE ${temp} INCLUDING ALL)
PARTITION BY RANGE (${column_identifier});

ALTER TABLE ${schema}.${table}
ALTER COLUMN ${column_identifier} SET NOT NULL;

SELECT partman.create_parent(
    p_parent_table := ${parent_table},
    p_control := ${column},
    p_type := 'range',
    p_interval := ${interval}
);

UPDATE partman.part_config
SET retention = ${retention},
    retention_keep_table = false,
    premake = 10
WHERE parent_table = ${parent_table};

DROP TABLE ${temp};
