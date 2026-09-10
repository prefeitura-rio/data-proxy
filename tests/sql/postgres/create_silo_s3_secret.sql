SELECT duckdb.raw_query(
    'CREATE OR REPLACE SECRET silo_s3 ('
    || 'TYPE S3, KEY_ID ''minioadmin'', SECRET ''minioadmin'', '
    || 'ENDPOINT ''${endpoint}'', URL_STYLE ''path'', USE_SSL false, '
    || 'REGION ''us-east-1'')'
)
