CREATE TABLE IF NOT EXISTS ${schema}.${table} AS
SELECT * FROM duckdb.query('SELECT * FROM read_parquet(''${gcs_path}'') LIMIT 0')
