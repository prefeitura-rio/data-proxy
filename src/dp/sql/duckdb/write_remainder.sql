COPY (
    SELECT ${columns} FROM bigquery_scan(${bq_table})
    WHERE ${partition_column} IS NULL
       OR ${partition_column} < ${partition_lower}
       OR ${partition_column} >= ${partition_upper}
) TO ${gcs_path} (FORMAT PARQUET)
