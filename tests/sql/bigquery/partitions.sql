SELECT struct_pack(
    partition_id := partition_id,
    last_modified_time := MAX(last_modified_time)
) AS partition
FROM partition_metadata
WHERE table_name = ?
GROUP BY partition_id
