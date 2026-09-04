SELECT struct_pack(
    table_name := table_name,
    table_type := table_type,
    partition_kind := partition_kind,
    partition_field := partition_field,
    range_start := range_start,
    range_end := range_end,
    range_interval := range_interval,
    time_granularity := time_granularity,
    modified := modified
) AS metadata
FROM table_metadata
WHERE table_name = '$table_name'
