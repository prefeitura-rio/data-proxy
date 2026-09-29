{#
{
  "kind": "template",
  "description": "Load DuckDB extensions and create the S3 access secret.",
  "inputs": {
    "s3_key_id": "S3 access key used by DuckDB.",
    "s3_secret_key": "S3 secret key used by DuckDB.",
    "s3_endpoint": "S3-compatible endpoint used by DuckDB.",
    "s3_use_ssl": "Whether DuckDB uses TLS for S3."
  }
}
#}
-- noqa: PRS
LOAD httpfs;
LOAD ducklake;
LOAD sqlite;
LOAD bigquery;
LOAD postgres_scanner;
CREATE SECRET (
    TYPE s3,
    KEY_ID {{ s3_key_id }},
    SECRET {{ s3_secret_key }},
    ENDPOINT {{ s3_endpoint }},
    URL_STYLE 'path',
    USE_SSL {{ s3_use_ssl }},
    REGION 'us-east-1'
)
