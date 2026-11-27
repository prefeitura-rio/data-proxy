# DuckLake write path

This page shows how the sync layer writes the data of a schema to its DuckLake catalog. [Sync layer](./sync.md) shows the whole sync run.

A publish worker commits one schema at a time. It uses DuckDB with the DuckLake extension.

```mermaid
%% DuckLake publication sequence
sequenceDiagram
  participant wk as Publish worker
  participant dd as DuckDB (ducklake)
  participant s3 as SeaweedFS
  participant cat as ducklake database
  participant app as Application database
  participant sb as Standby
  rect rgba(128, 128, 128, 0.15)
    Note over wk,sb: Commit the snapshot
    wk->>dd: attach the catalog
    dd->>cat: read the metadata
    wk->>dd: create tables, set partitions and sort
    dd->>s3: read scratch Parquet
    dd->>s3: write DuckLake data files
    dd->>cat: commit one snapshot
  end
  rect rgba(128, 128, 128, 0.15)
    Note over wk,sb: Record the state
    wk->>app: write data_proxy.state
  end
  rect rgba(128, 128, 128, 0.15)
    Note over wk,sb: Reach the readers
    cat->>sb: WAL replication
    sb->>sb: next query sees the snapshot
  end
  rect rgba(128, 128, 128, 0.15)
    Note over wk,sb: Maintain, also after a failed commit
    wk->>dd: run maintenance
    dd->>cat: expire snapshots
    dd->>s3: remove old files, merge files
  end
```

- **One writer for each schema:** publishing is parallel across schemas and sequential within each schema. One `publish` queue serves all schemas, and the schema name is its partition key. A new schema needs no new queue.
- **Files first:** the writer writes the Parquet files before it commits the snapshot.
- **State after each table:** the writer saves the signature of a table right after the commit of that table. A failed publish keeps the tables that committed, and the next run skips them.
- **Retry:** a commit retries while the catalog is locked. `DUCKLAKE_COMMIT_MAX_ATTEMPTS` and `DUCKLAKE_COMMIT_RETRY_SECONDS` set the limit.
- **Maintenance:** it runs after each commit of a schema, also when the commit publishes nothing or fails. A full storage fails every commit, and only the maintenance deletes the files that free it. A schema with a change that didn't commit stays in the plan, so each run of the schema maintains it again. The maintenance removes the old files before it merges, because a merge writes files. A maintenance error is logged and doesn't stop the sync run. A commit error still fails the run after the maintenance.
- **Retention:** `DUCKLAKE_SNAPSHOT_EXPIRATION` (7 days by default) keeps the files that an older snapshot uses. The CNPG backups cover the catalogs.

See [DuckLake read path](./serving.md#ducklake-read-path) for how a catalog reaches the readers.

<!-- nav:start -->
---

[Previous: Sync layer](./sync.md) | [Next: Console layer](./console.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
