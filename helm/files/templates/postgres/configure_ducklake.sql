{#
{
  "kind": "template",
  "description": "Persist the DuckLake S3 credentials and the catalog reader credentials for new pg_duckdb sessions.",
  "inputs": {
    "s3_key_id": "S3 access key.",
    "s3_secret_key": "S3 secret key.",
    "s3_endpoint": "S3 endpoint.",
    "s3_use_ssl": "Whether S3 uses TLS.",
    "reader_password": "Password of the ducklake_reader role."
  }
}
#}
-- noqa: disable=LT05
SELECT duckdb.raw_query(
    format(
        'CREATE OR REPLACE PERSISTENT SECRET s3 (TYPE S3, KEY_ID %L, SECRET %L, ENDPOINT %L, URL_STYLE ''path'', USE_SSL %s, REGION ''us-east-1'')',
        '{{ s3_key_id }}',
        '{{ s3_secret_key }}',
        '{{ s3_endpoint }}',
        '{{ s3_use_ssl }}'
    )
);
SELECT duckdb.raw_query(
    format(
        'CREATE OR REPLACE PERSISTENT SECRET ducklake_reader_pg (TYPE postgres, HOST ''localhost'', PORT 5432, DATABASE ''ducklake'', USER ''ducklake_reader'', PASSWORD %L)',
        '{{ reader_password }}'
    )
);
SELECT duckdb.raw_query(
    'CREATE OR REPLACE PERSISTENT SECRET ducklake_reader (TYPE ducklake, METADATA_PATH '''', METADATA_PARAMETERS MAP {''TYPE'': ''postgres'', ''SECRET'': ''ducklake_reader_pg''})'
)
