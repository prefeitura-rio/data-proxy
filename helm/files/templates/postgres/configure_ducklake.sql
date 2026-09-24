{#
{
  "kind": "template",
  "description": "Persist DuckLake S3 credentials for new pg_duckdb sessions.",
  "inputs": {
    "s3_key_id": "S3 access key.",
    "s3_secret_key": "S3 secret key.",
    "s3_endpoint": "S3 endpoint.",
    "s3_use_ssl": "Whether S3 uses TLS."
  }
}
#}
SELECT duckdb.raw_query(
    format(
        'CREATE OR REPLACE PERSISTENT SECRET s3 (TYPE S3, KEY_ID %L, SECRET %L, ENDPOINT %L, URL_STYLE ''path'', USE_SSL %s, REGION ''us-east-1'')',
        '{{ s3_key_id }}',
        '{{ s3_secret_key }}',
        '{{ s3_endpoint }}',
        '{{ s3_use_ssl }}'
    )
)
