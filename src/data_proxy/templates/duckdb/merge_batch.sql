{#
{
  "kind": "template",
  "description": "Render the merge batch database operation.",
  "inputs": {
    "scratch_path": "SQL-safe temporary Parquet path literal.",
    "path": "SQL-safe Parquet or object-storage path literal."
  }
}
#}
COPY (
    SELECT * FROM read_parquet({{ scratch_path }})
) TO {{ path }} (FORMAT PARQUET)
