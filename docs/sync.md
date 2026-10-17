# Sync

The sync configuration is a JSON document that declares PostgreSQL schemas, their ingestion sources, and source tables. Each sync run loads the document once, at its start, so a change never reaches a run that already started. The tables of the `console` schema hold the configuration ([Console](console.md#configuration-tables)), and the engine reads it with `console.sync_config()`. An empty configuration means there is nothing to sync.

```json
{
  "schemas": {
    "my_schema": {
      "source": { "type": "bigquery" },
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
| `source` | No | One external source for every table in this schema. Defaults to `{ "type": "bigquery" }`. |
| `claim`  | When a table uses `rls` | JWT claim matched against `access_policy.subject`.  |
| `tables` | No                      | Tables in this PostgreSQL schema. Defaults to `[]`. |
| `ducklake` | No                    | Schema-level DuckLake settings, including `encrypted`. |

The schema key is the target PostgreSQL schema. Don't add a schema field to a table entry.

## Table fields

| Field       | Required | Meaning                                                                               |
| ----------- | -------- | ------------------------------------------------------------------------------------- |
| `name`      | Yes      | Reference validated by the schema source. BigQuery uses `project.dataset.table`. |
| `strategy`  | Yes      | `full` replaces the DuckLake table; `partitioned` updates physical partitions.        |
| `n`         | No       | Keep the newest `n` time partitions.                                                  |
| `fallback` | Partitioned only | Allow the configured source to serve uncovered partitions when it has fallback metadata. Default: `false`. |
| `cache_ttl` | No       | Proxy cache lifetime in seconds.                                                      |
| `rls`       | No       | Unit column and unit type pairs. See [Security](security.md).                         |
| `ducklake` | No | Table-level DuckLake settings, including `sort` and `partitioning`. |
| `ducklake` | No | Table-level DuckLake settings, including custom partition transforms. |

## Pipeline

The scheduled `run_sync` workflow performs these steps:

1. Build the changed-table and changed-partition plan.
2. Enqueue source dump tasks.
3. Write independent scratch Parquet files to the S3 scratch path. Extraction doesn't merge files.
4. Run `seed_schemas` in parallel with publication. Seed reconciles PostgreSQL functions and views.
5. Enqueue one `publish_schema` workflow per schema.
6. DBOS sync workers insert scratch Parquet into DuckLake and commit a snapshot to the schema catalog in the `ducklake` database. Streaming replication copies the catalog to every standby.
7. After all publishers finish, tell PostgREST to reload its schema, only when seed added or removed views.
8. Flush scratch objects and Redis.

Publishing is parallel across schemas and sequential within a schema queue. There is one active writer for each schema catalog.

### DuckLake maintenance

After a publication commits, the same `publish_schema` workflow runs `apply_ducklake_maintenance` on the schema writer on the schema writer. It does the following in order:

1. Expire snapshots older than `DUCKLAKE_SNAPSHOT_EXPIRATION`.
2. Merge up to `DUCKLAKE_MAX_COMPACTED_FILES` adjacent files per table.
3. Rewrite files whose deleted fraction reaches `DUCKLAKE_REWRITE_DELETE_THRESHOLD`.
4. Clean old and orphaned files older than `DUCKLAKE_SNAPSHOT_EXPIRATION`.

Merging and rewriting create new snapshots, so the step returns the current snapshot, and the reader check waits for that one. A maintenance error is logged and doesn't stop the run: the published snapshot still reaches the readers, and the next publication retries the maintenance. Schemas that publish nothing aren't maintained.

The maintenance CronJob only cleans stale PostgreSQL objects.

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

Before publication, the writer compares each incoming Parquet schema with the existing DuckLake table. It adds new nullable columns and applies only lossless type promotions. It rejects removed columns, renames, and incompatible type changes before changing the table. Inserts use column names, not column positions. Existing Parquet files aren't rewritten for these schema changes.

## Catalog lifecycle

Each schema uses these locations:

```text
catalog:         database ducklake, schema <schema> (DuckLake metadata, written by the sync workers)
secrets:         data-proxy-duckdb:/duckdb/secrets (PostgreSQL pods, mounted at ~/.duckdb/stored_secrets)
DuckLake data:   s3://<bucket>/ducklake/<schema>/<table>/*.parquet
```

The DBOS sync workers write the catalog as the `ops` role, through the primary. PostgreSQL pods read it as the `duckdb` role, from their own instance, so a standby never reads from the primary. A PostgreSQL backend that attached earlier sees a new snapshot at the start of its next query, so nothing refreshes the readers. See [Architecture](architecture.md#catalog-database).

## Partition fallback

Set `fallback: true` on a partitioned table to let its configured schema source serve data that DuckLake doesn't hold. For a BigQuery schema, the table function reads published partitions from DuckLake and every other partition from BigQuery.

```json
{
  "name": "project.dataset.events",
  "strategy": "partitioned",
  "fallback": true
}
```

`fallback` is invalid on full tables. It's also invalid when the configured source has no fallback metadata. Without it, the table function reads DuckLake only. See [Proxy](proxy.md).

## Adding a source

A source owns synchronization ingestion. It validates references, creates DuckDB scan expressions, reports modification and partition state, caches external clients, and declares DuckDB extensions. Register the source in `sync/src/sync/sources/registry.py`.

Each PostgreSQL schema has exactly one source. Add `source.settings` only when a source requires non-secret settings. Put credentials in environment variables or Kubernetes Secrets, not in `sync.json`.

DuckLake is the fixed local primary source. The configured schema source is the only possible partition fallback.

## Partitions

A `full` table creates one dump task. A partitioned table creates one extraction task for its changed physical partitions. Each selection writes one Parquet file.

| Source type       | `n`       | `__NULL__`                        |
| ----------------- | --------- | --------------------------------- |
| Time partitioned  | Supported | Ignored                           |
| Range partitioned | Rejected  | Synced as the remainder partition |

`__UNPARTITIONED__` is unsupported and stops planning.

## Schema initialization

The provision job creates configured schemas, `access_policy`, `access_log`, policies, and triggers. It doesn't create application data tables in PostgreSQL. The sync workflow creates the views. Application table names are PostgreSQL views over DuckLake functions.

---

[← Previous](architecture.md) · [Home](../README.md) · [Next →](using.md)
