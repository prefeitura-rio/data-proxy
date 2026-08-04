SELECT DISTINCT ${partition_column}
FROM bigquery_scan('${bq_table}')
WHERE ${partition_column} IS NOT NULL
ORDER BY ${partition_column} DESC
LIMIT ${n}
