# Sync

Set `SYNC_CONFIG_PATH` to a JSON file that declares PostgreSQL schemas and BigQuery tables.

```json
{
  "schemas": {
    "my_schema": {
      "claim": "preferred_username",
      "tables": [
        {
          "name": "project.dataset.events",
          "strategy": "partitioned",
          "n": 7,
          "indexes": [{ "name": "idx_events_unit", "columns": ["unit_id"] }],
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

The schema key is the target PostgreSQL schema. Do not add a schema field to a table entry.

## Table fields

| Field       | Required | Meaning                                                                               |
| ----------- | -------- | ------------------------------------------------------------------------------------- |
| `name`      | Yes      | BigQuery reference: `project.dataset.table`.                                          |
| `strategy`  | Yes      | `full` replaces the DuckLake table; `partitioned` updates physical partitions.        |
| `n`         | No       | Keep the newest `n` time partitions.                                                  |
| `fallback`  | No       | Enables the `_bq` fallback view. Default: `true`.                                     |
| `cache_ttl` | No       | Fallback cache lifetime in seconds.                                                   |
| `rls`       | No       | Unit column and unit type pairs. See [Security](security.md).                         |
| `indexes`   | No       | DuckLake sort columns. These are rendered as `SET SORTED BY`, not PostgreSQL indexes. |

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
9. Expire old DuckLake snapshots and clean unreferenced data files.
10. Flush scratch objects and Valkey.

Publishing is parallel across schemas and sequential within a schema queue. There is one active writer for each schema catalog.

## Catalog lifecycle

Each schema uses these locations:

```text
local writer:    /var/lib/ducklake/writer/<schema>/catalog.sqlite
local reader:    /var/lib/ducklake/catalogs/<schema>/catalog.sqlite
Litestream S3:   s3://<bucket>/ducklake/<schema>/catalog.sqlite/ (LTX replica prefix)
DuckLake data:   s3://<bucket>/ducklake/<schema>/<table>/*.parquet
```

The DBOS sync writes the writer catalog volume. Litestream replicates the writer volume and restores the separate reader catalog volume. CNPG PostgreSQL instances mount the reader volume read-only. pg_duckdb re-reads the catalog file on each query.

## Fallback per table

Fallback has two gates. Chart fallback must be enabled and the table must have `fallback: true`.

```json
{
  "name": "project.dataset.internal_table",
  "strategy": "full",
  "fallback": false
}
```

When fallback is disabled, the `_bq` view is not created. Nginx returns the DuckLake view response without a BigQuery fallback query.

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
