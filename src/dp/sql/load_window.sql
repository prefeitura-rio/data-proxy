DELETE FROM pg.${schema}.${table_name}
WHERE ${partition_column} = '${partition_value}';
INSERT INTO pg.${schema}.${table_name}
SELECT * FROM read_parquet('${gcs_path}')
