{#
{
  "kind": "template",
  "description": "Attach one SQLite DuckLake catalog for writing.",
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
    DATA_INLINING_ROW_LIMIT 0{% if encrypted %},
    ENCRYPTED{% endif %}
)
