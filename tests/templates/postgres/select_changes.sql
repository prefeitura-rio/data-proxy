{#
{
  "kind": "template",
  "description": "Count the changed rows per snapshot and change type. Parameters: start_snapshot, end_snapshot.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
SELECT change_snapshot_id, change_type, count(*) AS row_count
FROM {{ schema }}.ducklake_changes_people(%(start_snapshot)s, %(end_snapshot)s)
GROUP BY change_snapshot_id, change_type
ORDER BY change_snapshot_id
