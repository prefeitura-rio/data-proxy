SELECT
    partition_id,
    MAX(last_modified_time) AS last_modified_time
FROM `${project}.${dataset}.INFORMATION_SCHEMA.PARTITIONS`
WHERE table_name = @table_name
GROUP BY partition_id
