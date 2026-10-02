# Sync

Set `SYNC_CONFIG_PATH` to a JSON file that declares PostgreSQL schemas and BigQuery tables.

```json
{
  "schemas": {
    "my_schema": {
      "claim": "preferred_username",
      "ducklake": { "encrypted": false },
      "tables": [
        {
          "name": "project.dataset.events",
          "strategy": "partitioned",
          "n": 7,
          "ducklake": { "sort": ["event_time", "unit_id"] },
          "rls": [{ "column": "unit_id", "unit_type": "unit" }]
        }
      ]
    }
  }
}
```

## Schema fields

| Field    | Required                | Meaning                                             |
| -------- | ----------------------- | --------------------------------------------------- |
| `claim`  | When a table uses `rls` | JWT claim matched against `access_policy.subject`.  |
| `tables` | No                      | Tables in this PostgreSQL schema. Defaults to `[]`. |
| `ducklake` | No                    | Schema-level DuckLake settings, including `encrypted`. |

The schema key is the target PostgreSQL schema. Do not add a schema field to a table entry.

## Table fields

| Field       | Required | Meaning                                                                               |
| ----------- | -------- | ------------------------------------------------------------------------------------- |
| `name`      | Yes      | BigQuery reference: `project.dataset.table`.                                          |
| `strategy`  | Yes      | `full` replaces the DuckLake table; `partitioned` updates physical partitions.        |
| `n`         | No       | Keep the newest `n` time partitions.                                                  |
| `fallbacks` | No       | Ordered list of source names queried after DuckLake for uncovered rows, e.g. `["bigquery"]`. Default: `[]`.         |
| `cache_ttl` | No       | Proxy cache lifetime in seconds.                                                      |
| `rls`       | No       | Unit column and unit type pairs. See [Security](security.md).                         |
| `ducklake` | No | Table-level DuckLake settings, including `sort` and `partitioning`. |
| `ducklake` | No | Table-level DuckLake settings, including custom partition transforms. |

## Pipeline

The scheduled `run_sync` workflow performs these steps:

1. Build the changed-table and changed-partition plan.
2. Enqueue BigQuery dump tasks.
3. Write independent scratch Parquet files to the S3 scratch path. Extraction does not merge files.
4. Run `seed_schemas` in parallel with publication. Seed reconciles PostgreSQL functions and views.
5. Enqueue one `publish_schema` workflow per schema.
6. DBOS sync workers insert scratch Parquet into DuckLake and commit the writer catalog volume.
7. Litestream replicates writer catalog WAL changes to SeaweedFS.
8. Litestream restore updates the reader catalog volume.
9. Expire snapshots older than seven days.
10. Merge up to the configured number of adjacent files.
11. Rewrite files only when the deleted fraction reaches the configured threshold.
12. Clean scheduled and orphaned files older than seven days.
13. Flush scratch objects and Valkey.

Publishing is parallel across schemas and sequential within a schema queue. There is one active writer for each schema catalog. Maintenance runs on the same schema writer.

## DuckLake partitioning and encryption

DuckLake partitioning follows the source partition column by default. A table can override it with transforms such as:

```json
"ducklake": {
  "partitioning": [
    { "column": "event_time", "transform": "month" },
    { "column": "unit_id", "transform": "bucket", "buckets": 16 }
  ]
}
```

Changing a table transform triggers a full table rewrite before the new layout is published. Encryption is configured only under the schema-level `ducklake.encrypted` setting. Table-level encryption is rejected because DuckLake encryption is catalog scoped.

## Schema evolution

Before publication, the writer compares each incoming Parquet schema with the existing DuckLake table. It adds new nullable columns and applies only lossless type promotions. It rejects removed columns, renames, and incompatible type changes before changing the table. Inserts use column names, not column positions. Existing Parquet files are not rewritten for these schema changes.

## Catalog lifecycle

Each schema uses these locations:

```text
writer volume:   /var/lib/ducklake/catalogs/<schema>/catalog.sqlite (sync pods)
                 /var/lib/ducklake/writer/<schema>/catalog.sqlite   (Litestream replicate)
reader volume:   /var/lib/ducklake/catalogs/<schema>/catalog.sqlite (Litestream restore, PostgreSQL)
Litestream S3:   s3://<bucket>/ducklake/<schema>/catalog.sqlite/ (LTX replica prefix)
DuckLake data:   s3://<bucket>/ducklake/<schema>/<table>/*.parquet
```

The DBOS sync writes the writer catalog volume. Litestream replicates the writer volume and restores the separate reader catalog volume. CNPG PostgreSQL instances mount the reader volume read-only. A PostgreSQL backend keeps its DuckLake attachment. After a publication, the workflow waits until the reader catalog has the new snapshot and then restarts the Pooler deployments, so new backends attach the current catalog.

## Fallback per table

Set `fallbacks: ["bigquery"]` on a table to let BigQuery serve the data that DuckLake does not hold. For a partitioned table, the table function reads the published partitions from DuckLake and every other partition from BigQuery. For a table that is not published yet, it reads BigQuery alone.

```json
{
  "name": "project.dataset.events",
  "strategy": "partitioned",
  "fallbacks": ["bigquery"]
}
```

Without `fallbacks`, the table function reads DuckLake only. Each request to a table with `fallbacks` also queries each listed source, so enable it only for tables where that cost is acceptable. See [Proxy](proxy.md).

## Adding a data source

Each source is registered in `src/data_proxy/sources/sources.py` as a `Source` dataclass with its DuckDB load statement and scan expression. To add a new engine, register one entry and create a template file `src/data_proxy/templates/postgres/sources/sources/<name>.sql` with the helper function body. No other files change.

## Partitions

A `full` table creates one dump task. A partitioned table creates one extraction task for its changed physical partitions. Each selection writes one Parquet file.

| Source type       | `n`       | `__NULL__`                        |
| ----------------- | --------- | --------------------------------- |
| Time partitioned  | Supported | Ignored                           |
| Range partitioned | Rejected  | Synced as the remainder partition |

`__UNPARTITIONED__` is unsupported and stops planning.

## Schema initialization

The sync workflow creates configured schemas, `access_policy`, `access_log`, policies, triggers, and `policy_writer_<schema>` roles. It does not create application data tables in PostgreSQL. Application table names are PostgreSQL views over DuckLake functions.

---

[← Previous](architecture.md) · [Home](../README.md) · [Next →](using.md)
