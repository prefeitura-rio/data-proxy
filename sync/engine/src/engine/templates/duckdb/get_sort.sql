{#
{
  "kind": "template",
  "description": "Read the active DuckLake sort expressions for one table.",
  "inputs": {
    "metadata": "Name of the attached DuckLake metadata catalog, followed by its schema when the catalog has schemas."
  }
}
#}
-- noqa: disable=PRS,AM05
SELECT expression_info.expression
FROM {{ metadata }}.ducklake_sort_info AS info
JOIN {{ metadata }}.ducklake_sort_expression AS expression_info
  ON info.sort_id = expression_info.sort_id
JOIN {{ metadata }}.ducklake_table AS table_info
  ON info.table_id = table_info.table_id
WHERE table_info.table_name = ?
  AND table_info.end_snapshot IS NULL
  AND info.end_snapshot IS NULL
ORDER BY expression_info.sort_key_index
