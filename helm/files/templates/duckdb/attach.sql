{#
{
  "kind": "template",
  "description": "Attach one SQLite DuckLake catalog for maintenance.",
  "inputs": {
    "catalog": "SQL literal full DuckLake attach URI, e.g. ducklake:sqlite:<path>.",
    "data_path": "SQL literal DuckLake data path.",
    "encrypted": "Whether the catalog uses encrypted Parquet files."
  }
}
#}
-- noqa: disable=PRS
ATTACH {{ catalog }} AS dl (
    DATA_PATH {{ data_path }},
    DATA_INLINING_ROW_LIMIT 0,
    BUSY_TIMEOUT 5000{% if encrypted %},
    ENCRYPTED{% endif %}
)
