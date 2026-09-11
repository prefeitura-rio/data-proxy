SELECT duckdb.raw_query(
    format(
        'CREATE OR REPLACE PERSISTENT SECRET gcs (TYPE s3, KEY_ID %L, SECRET %L, REGION ''us-east-1'', ENDPOINT %L, URL_STYLE ''path'', USE_SSL %s)',
        :'gcs_key_id',
        :'gcs_secret_key',
        :'gcs_endpoint',
        :'gcs_use_ssl'
    )
)
