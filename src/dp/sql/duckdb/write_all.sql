COPY (SELECT ${columns} FROM bigquery_scan(${bq_table})) TO ${gcs_path} (
    FORMAT PARQUET
)
