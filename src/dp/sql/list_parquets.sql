SELECT path FROM glob('gs://${gcs_bucket}/${table_name}/**/*.parquet')
