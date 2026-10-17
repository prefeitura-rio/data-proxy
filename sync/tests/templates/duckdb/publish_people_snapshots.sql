{#
{
  "kind": "template",
  "description": "Publish two DuckLake snapshots of people the way the sync worker does: Parquet files only, and a sort order set with the first snapshot.",
  "inputs": {
    "catalog_url": "libpq URI of the catalog database.",
    "metadata_schema": "Schema of the catalog database that holds the DuckLake tables.",
    "data_path": "DuckLake data path.",
    "endpoint": "Silo host and port.",
    "first": "Parquet path of the first snapshot.",
    "second": "Parquet path of the second snapshot."
  }
}
#}
-- noqa: disable=PRS
INSTALL httpfs;
LOAD httpfs;
CREATE OR REPLACE SECRET silo_s3 (
    TYPE S3, KEY_ID 'minioadmin', SECRET 'minioadmin',
    ENDPOINT '{{ endpoint }}', URL_STYLE 'path', USE_SSL false, REGION 'us-east-1'
);
ATTACH 'ducklake:postgres:{{ catalog_url }}' AS lake (
    DATA_PATH '{{ data_path }}', DATA_INLINING_ROW_LIMIT 0,
    METADATA_SCHEMA '{{ metadata_schema }}'
);
BEGIN;
CREATE TABLE lake.people AS SELECT * FROM read_parquet('{{ first }}');
ALTER TABLE lake.people SET SORTED BY (cpf);
COMMIT;
INSERT INTO lake.people SELECT * FROM read_parquet('{{ second }}');
