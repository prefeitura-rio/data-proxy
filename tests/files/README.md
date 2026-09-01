# BigQuery fixture cases

`metadata.csv` defines the table metadata used by the BigQuery fake. `partitions.csv` defines the partition rows for the same tables.

The cases cover:

- Plain and missing-modification tables: `plain`, `missing_modified`.
- Range tables: normal ranges, non-zero starts, null buckets, views, missing or invalid metadata, and invalid partition IDs.
- Time tables: `DAY`, `HOUR`, `MONTH`, and `YEAR` partitions, plus null, unsupported, ingestion-time, and invalid partition cases.

Test names in `test_bigquery.py` identify the case that each row supports.
