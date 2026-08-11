COPY (
    SELECT ${columns} FROM bigquery_scan('${bq_table}')
    WHERE ${partition_column} = '${partition_value}'
) TO '${gcs_path}' (FORMAT PARQUET)
