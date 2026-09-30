{#
{
  "kind": "template",
  "description": "Return the latest snapshot ID that the reader catalog has applied.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the snapshot function."
  }
}
#}
SELECT {{ schema }}.ducklake_latest_snapshot()
