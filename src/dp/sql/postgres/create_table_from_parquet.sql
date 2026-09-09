DROP TABLE IF EXISTS ${schema}.${table};
CREATE TABLE ${schema}.${table}
AS SELECT * FROM read_parquet(${gcs_path})
