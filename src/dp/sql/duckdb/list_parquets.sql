SELECT file FROM glob('s3://${gcs_bucket}/${table_name}/**/*.parquet')
