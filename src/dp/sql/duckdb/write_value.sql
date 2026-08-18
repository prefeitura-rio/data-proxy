COPY (
    SELECT ${columns} FROM bigquery_scan(${bq_table})
    WHERE ${column} = ${value}
) TO ${gcs_path} (FORMAT PARQUET)
