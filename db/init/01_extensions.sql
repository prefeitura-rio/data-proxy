CREATE EXTENSION IF NOT EXISTS pg_duckdb;

SELECT duckdb.raw_query(E'INSTALL httpfs');
SELECT duckdb.raw_query(E'LOAD httpfs');
SELECT duckdb.raw_query(E'CREATE PERSISTENT SECRET s3_minio (TYPE s3, KEY_ID ''minioadmin'', SECRET ''minioadmin'', ENDPOINT ''minio:9000'', URL_STYLE ''path'', USE_SSL false, REGION ''us-east-1'')');
