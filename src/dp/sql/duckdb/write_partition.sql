COPY (
    SELECT ${columns} FROM bigquery_scan(${bq_table})
    WHERE ${partition_column} >= ${partition_lower}
      AND ${partition_column} < ${partition_upper}
) TO ${gcs_path} (FORMAT PARQUET)
