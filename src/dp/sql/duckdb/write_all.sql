COPY (SELECT ${columns} FROM bigquery_scan(${bq_table})) TO ${path} (
    FORMAT PARQUET
)
