DELETE FROM pg.${schema}.${table_name};
INSERT INTO pg.${schema}.${table_name} SELECT * FROM read_parquet('${gcs_path}')
