SELECT DISTINCT ${partition_column}
FROM bigquery_scan('${bq_table}')
LIMIT ${n}
