{#
{
  "kind": "template",
  "description": "Count the active DuckLake partition keys of one table.",
  "inputs": {
    "metadata": "Name of the attached DuckLake metadata catalog, followed by its schema when the catalog has schemas."
  }
}
#}
-- noqa: disable=PRS,AM05
-- The SQLFluff DuckDB dialect can't parse this statement.
SELECT count(*)
FROM {{ metadata }}.ducklake_partition_column AS key
JOIN {{ metadata }}.ducklake_partition_info AS info
  ON key.partition_id = info.partition_id
JOIN {{ metadata }}.ducklake_table AS table_info
  ON info.table_id = table_info.table_id
WHERE table_info.table_name = ?
  AND table_info.end_snapshot IS NULL
  AND info.end_snapshot IS NULL
