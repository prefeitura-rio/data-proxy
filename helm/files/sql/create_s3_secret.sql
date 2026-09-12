SELECT duckdb.raw_query(
    format(
        'CREATE OR REPLACE PERSISTENT SECRET s3 (TYPE s3, KEY_ID %L, SECRET %L, REGION ''us-east-1'', ENDPOINT %L, URL_STYLE ''path'', USE_SSL %s)',
        :'s3_key_id',
        :'s3_secret_key',
        :'s3_endpoint',
        :'s3_use_ssl'
    )
)
