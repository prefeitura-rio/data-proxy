CREATE OR REPLACE TABLE pg.${schema}.${table}
AS SELECT * FROM read_parquet(${gcs_path}) LIMIT 0
