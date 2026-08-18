COPY (
    SELECT ${columns} FROM bigquery_scan(${bq_table})
    WHERE ${column} IS NULL
       OR ${column} < ${lower}
       OR ${column} >= ${upper}
) TO ${gcs_path} (FORMAT PARQUET)
