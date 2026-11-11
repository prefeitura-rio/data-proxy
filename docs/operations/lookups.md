# Lookup Layout

These settings change how a DuckLake table is laid out in the bucket, so a lookup on one value reads fewer files. A table with no setting works, and the settings help a large table. Use them in the table configuration. The memory values are on the page [DuckDB Memory and Spill](duckdb.md).

## Partition key of an integer range table

An integer range table doesn't need a partition key for a table of a few gigabytes. The batches of a run are contiguous key ranges, so the minimum and maximum of each file already skip most files. A test with 2.75 million rows (2.4 GB) at a DuckDB memory limit of 2 GB gave these results.

| Layout | Files | Insert time | Peak memory | Files read by a lookup on one value |
| --- | --- | --- | --- | --- |
| No key | 4 | 23 s | 1.2 GB | 1 |
| `bucket` with 8 | 8 | 26 s | 2.3 GB | 1 |
| `bucket` with 16 | 16 | 38 s | 2.5 GB | 1 |
| `SET SORTED BY` the column | none | none | out of memory | none |

A `bucket` key reads every bucket for a range query, because it spreads neighbouring values. Set a `bucket` transform in the table configuration only when the table has hundreds of files and its queries look up one value.

Never use the `identity` transform on a column with a different value in each row. DuckLake makes one partition for each value, and the commit runs out of memory.

## Sort columns of an integer range table

A sort on the key gives each file tight row groups, so a lookup on one value reads one row group. DuckLake sorts the rows of one insert in memory. A sorted table therefore gets one insert for each batch file, and a sort keeps one batch and not the whole table. The same test as above, with the table sorted by the key, gave these results.

| Commit | Files | Time | Peak memory | Row groups read by a lookup |
| --- | --- | --- | --- | --- |
| No sort | 4 | 20 s | 1.2 GB | 3.8 |
| Sorted, one insert for each batch, 2 GB limit | 10 | 12 s | 1.3 GB | 1.0 |
| Sorted, one insert for all batches, 2 GB limit | none | none | out of memory | none |
| Sorted, one insert for all batches, 4 GB limit | 5 | 31 s | 4.2 GB | 1.0 |

A sort in the extraction query alone, with no sort on the table, gave 1.9 row groups for a lookup. Set the sort columns of a table in its configuration. Use the console import with `"ducklake": {"sort": ["cnpj_particao"]}`, or the `sort` list of the `ducklake` block of the Terraform resource. A change of the sort or of the partitioning makes the next sync rebuild the table.

The commit of a table runs its delete and its inserts in one DuckLake transaction. A failed insert leaves the table as it was.

<!-- nav:start -->
---

[Previous: DuckDB Memory and Spill](./duckdb.md) | [Next: Metrics](./metrics.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
