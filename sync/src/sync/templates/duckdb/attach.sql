{#
{
  "kind": "template",
  "description": "Attach one DuckLake catalog for writing.",
  "inputs": {
    "catalog": "SQL literal full DuckLake attach URI, e.g. ducklake:postgres:<URI>.",
    "data_path": "SQL literal DuckLake data path.",
    "metadata_schema": "Optional SQL literal schema that holds the catalog tables. SQLite catalogs have no schemas.",
    "encrypted": "Whether the catalog uses encrypted Parquet files."
  }
}
#}
-- noqa: disable=PRS
ATTACH {{ catalog }} AS dl (
    DATA_PATH {{ data_path }},
    DATA_INLINING_ROW_LIMIT 0,{% if metadata_schema is defined %}
    METADATA_SCHEMA {{ metadata_schema }},{% endif %}
    BUSY_TIMEOUT 5000{% if encrypted %},
    ENCRYPTED{% endif %}
)
