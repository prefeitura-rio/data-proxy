COPY (
    SELECT ${columns} FROM bigquery_scan(${bq_table})
    WHERE ${column} >= ${lower}
      AND ${column} < ${upper}
) TO ${gcs_path} (FORMAT PARQUET)
