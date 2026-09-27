{#
{
  "kind": "template",
  "description": "Read the active DuckLake sort expressions for one table.",
  "inputs": {}
}
#}
-- noqa: disable=PRS,AM05
SELECT expression_info.expression
FROM __ducklake_metadata_dl.ducklake_sort_info AS info
JOIN __ducklake_metadata_dl.ducklake_sort_expression AS expression_info
  ON expression_info.sort_id = info.sort_id
JOIN __ducklake_metadata_dl.ducklake_table AS table_info
  ON table_info.table_id = info.table_id
WHERE table_info.table_name = ?
  AND table_info.end_snapshot IS NULL
  AND info.end_snapshot IS NULL
ORDER BY expression_info.sort_key_index
