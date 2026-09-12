COPY (SELECT ${columns} FROM bigquery_scan(${bq_table})) TO ${s3_path} (
    FORMAT PARQUET
)
