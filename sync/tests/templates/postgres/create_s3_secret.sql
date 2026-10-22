{#
{
  "kind": "template",
  "description": "Create the pg_duckdb S3 secret for the test object storage.",
  "inputs": {
    "endpoint": "Storage host and port.",
    "access_key": "Storage access key.",
    "secret_key": "Storage secret key."
  }
}
#}
SELECT duckdb.raw_query(
    'CREATE OR REPLACE SECRET test_s3 ('
    || 'TYPE S3, KEY_ID ''{{ access_key }}'', SECRET ''{{ secret_key }}'', '
    || 'ENDPOINT ''{{ endpoint }}'', URL_STYLE ''path'', USE_SSL false, '
    || 'REGION ''us-east-1'')'
)
