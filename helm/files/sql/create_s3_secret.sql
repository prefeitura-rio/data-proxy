SELECT duckdb.create_simple_secret(
    type := 'S3',
    key_id := :'gcs_key_id',
    secret := :'gcs_secret_key',
    endpoint := :'gcs_endpoint',
    url_style := 'path',
    use_ssl := :'gcs_use_ssl',
    region := 'us-east-1'
)
