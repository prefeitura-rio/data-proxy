{#
{
  "kind": "template",
  "description": "Query BigQuery physical partition metadata.",
  "inputs": {
    "project": "BigQuery project identifier.",
    "dataset": "BigQuery dataset identifier."
  }
}
#}
SELECT
    partition_id,
    MAX(last_modified_time) AS last_modified_time,
    SUM(CAST(total_logical_bytes AS INT64)) AS logical_bytes
FROM `{{ project }}.{{ dataset }}.INFORMATION_SCHEMA.PARTITIONS`
WHERE table_name = @table_name
GROUP BY partition_id
