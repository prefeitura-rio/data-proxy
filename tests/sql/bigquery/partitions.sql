SELECT struct_pack(
    partition_id := partition_id,
    last_modified_time := MAX(last_modified_time),
    logical_bytes := SUM(CAST(total_logical_bytes AS INT64))
) AS partition
FROM partition_metadata
WHERE table_name = ?
GROUP BY partition_id
