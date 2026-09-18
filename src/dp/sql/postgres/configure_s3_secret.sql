{#
{
  "kind": "template",
  "description": "Configure the connection-local DuckDB S3 secret.",
  "inputs": {
    "s3_key_id": "S3 access key used by DuckDB.",
    "s3_secret_key": "S3 secret key used by DuckDB.",
    "s3_endpoint": "S3-compatible endpoint used by DuckDB.",
    "s3_use_ssl": "Whether DuckDB uses TLS for S3."
  }
}
#}
SELECT duckdb.raw_query(
    format(
        'CREATE OR REPLACE SECRET s3 ('
        || 'TYPE s3, KEY_ID %L, SECRET %L, REGION ''us-east-1'', '
        || 'ENDPOINT %L, URL_STYLE ''path'', USE_SSL %s)',
        '{{ s3_key_id }}',
        '{{ s3_secret_key }}',
        '{{ s3_endpoint }}',
        '{{ s3_use_ssl }}'
    )
)
