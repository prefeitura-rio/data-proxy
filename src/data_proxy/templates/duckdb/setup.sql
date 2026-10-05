{#
{
  "kind": "template",
  "description": "Load DuckDB extensions and create the S3 access secret.",
  "inputs": {
    "s3_key_id": "S3 access key used by DuckDB.",
    "s3_secret_key": "S3 secret key used by DuckDB.",
    "s3_endpoint": "S3-compatible endpoint used by DuckDB.",
    "s3_use_ssl": "Whether DuckDB uses TLS for S3.",
    "source_extensions": "DuckDB extension names required by configured sources."
  }
}
#}
-- noqa: PRS
LOAD httpfs;
LOAD ducklake;
LOAD sqlite;
{% for extension in source_extensions | default([]) %}
LOAD {{ extension }};
{% endfor %}
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
