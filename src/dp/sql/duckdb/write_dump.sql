COPY (SELECT * FROM bigquery_scan('${bq_table}')) TO '${gcs_path}' (
    FORMAT PARQUET
)
