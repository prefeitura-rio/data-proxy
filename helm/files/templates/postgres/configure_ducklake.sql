{#
{
  "kind": "template",
  "description": "Persist the DuckLake S3 credentials and the catalog reader credentials for new pg_duckdb sessions. Run it as a superuser: pg_duckdb turns the local file system off for every other role, and a persistent secret is a file.",
  "inputs": {
    "s3_key_id": "S3 access key.",
    "s3_secret_key": "S3 secret key.",
    "s3_endpoint": "S3 endpoint.",
    "s3_use_ssl": "Whether S3 uses TLS.",
    "duckdb_password": "Password of the duckdb role.",
    "catalog_port": "Port of the catalog database, as the PostgreSQL server sees it. The default is 5432."
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
        'CREATE OR REPLACE PERSISTENT SECRET ducklake_catalog_pg (TYPE postgres, HOST ''localhost'', PORT %s, DATABASE ''ducklake'', USER ''duckdb'', PASSWORD %L)',
        '{{ catalog_port | default(5432) }}',
        '{{ duckdb_password }}'
    )
);
SELECT duckdb.raw_query(
    'CREATE OR REPLACE PERSISTENT SECRET ducklake_catalog (TYPE ducklake, METADATA_PATH '''', METADATA_PARAMETERS MAP {''TYPE'': ''postgres'', ''SECRET'': ''ducklake_catalog_pg''})'
)
