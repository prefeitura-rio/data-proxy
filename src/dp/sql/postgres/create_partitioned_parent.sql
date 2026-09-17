{#
{
  "kind": "template",
  "description": "Render the create partitioned parent database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed.",
    "temp": "Temporary table used during the operation.",
    "column": "SQL-safe identifier for the partition or source column.",
    "parent_table": "Partitioned parent table passed to pg_partman.",
    "column_name": "Partition column name passed to the partition manager.",
    "interval": "Partition interval passed to pg_partman.",
    "retention": "Partition retention period passed to pg_partman."
  }
}
#}
CREATE TABLE {{ schema }}.{{ table }} (LIKE {{ temp }} INCLUDING ALL)
PARTITION BY RANGE ({{ column }});

ALTER TABLE {{ schema }}.{{ table }}
ALTER COLUMN {{ column }} SET NOT NULL;

SELECT partman.create_parent(
    p_parent_table := {{ parent_table }},
    p_control := {{ column_name }},
    p_type := 'range',
    p_interval := {{ interval }}
);

UPDATE partman.part_config
SET
    retention = {{ retention }},
    retention_keep_table = false,
    premake = 10
WHERE parent_table = {{ parent_table }};

DROP TABLE {{ temp }};
