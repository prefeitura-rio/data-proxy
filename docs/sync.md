# Sync

This page is the reference for the sync configuration. The sync configuration is a JSON document that declares PostgreSQL schemas, their ingestion sources and source tables. The field `source.type` names the source adapter, and `bigquery` is the only adapter today. Each sync run loads the document once, at its start, so a change never reaches a sync run that already started.

The tables of the `console` schema contain the configuration ([Console](console/configuration.md#configuration-tables)), and the engine reads it with `console.sync_config()`. An empty configuration means there is nothing to sync.

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

| Field      | Required                | Meaning                                                                                   |
| ---------- | ----------------------- | ----------------------------------------------------------------------------------------- |
| `source`   | No                      | One external source for every table in this schema. Defaults to `{ "type": "bigquery" }`. |
| `claim`    | When a table uses `rls` | JSON Web Token (JWT) claim matched against `access_policy.subject`.                       |
| `tables`   | No                      | Tables in this PostgreSQL schema. Defaults to `[]`.                                       |
| `ducklake` | No                      | Schema-level DuckLake settings, including `encrypted`.                                    |

The schema key is the target PostgreSQL schema. Don't add a schema field to a table entry.

The console exports and imports the same document grouped by project, and the schema key is then the short name. The import also reads the form above, and puts those schemas in the project `default`.

```json
{ "projects": { "sales": { "schemas": { "orders": { "tables": [] } } } } }
```

This document makes the schema `sales_orders`. The engine reads `sync_config()`, which lists each schema by its physical name.

## Table fields

These fields describe one table of a schema in `sync.json`. The table says which fields are required.

| Field       | Required         | Meaning                                                                                                    |
| ----------- | ---------------- | ---------------------------------------------------------------------------------------------------------- |
| `name`      | Yes              | Reference validated by the schema source. BigQuery uses `project.dataset.table`.                           |
| `strategy`  | Yes              | `full` replaces the DuckLake table, and `partitioned` updates physical partitions.                         |
| `n`         | No               | Keep the newest `n` time partitions.                                                                       |
| `fallback`  | Partitioned only | Allow the configured source to serve uncovered partitions when it has fallback metadata. Default: `false`. |
| `cache_ttl` | No               | Proxy cache lifetime in seconds.                                                                           |
| `rls`       | No               | Unit column and unit type pairs. See [Security](security/index.md).                                        |
| `ducklake`  | No               | Table-level DuckLake settings, including `sort` and `partitioning`.                                        |
| `ducklake`  | No               | Table-level DuckLake settings, including custom partition transforms.                                      |

## Pipeline

The scheduled `run_sync` workflow plans the changed tables and partitions, dumps them as scratch Parquet files and publishes each schema to its DuckLake catalog. Publishing runs in parallel across schemas and in sequence within a schema, so each catalog has one writer. After a publication, the workflow expires old snapshots, merges and rewrites files and cleans orphaned files. A maintenance error is logged and doesn't stop the sync run. [Sync layer](architecture/sync.md) lists the workflows and shows the sequence.

## DuckLake partitioning and encryption

DuckLake partitioning follows the source partition column by default only for a time partitioned source. An integer range source gives the table no partitioning. The column can have a different value in every row, and DuckLake makes one partition for each value. A table can set its own partitioning with transforms such as:

```json
"ducklake": {
  "partitioning": [
    { "column": "event_time", "transform": "month" },
    { "column": "unit_id", "transform": "bucket", "buckets": 16 }
  ]
}
```

An integer range table of a few gigabytes needs no partition key, because the batches of a run are contiguous key ranges. Set a `bucket` transform only for a table with hundreds of files. Never use `identity` on a column with a different value in each row. See [the measured layouts](operations/lookups.md#partition-key-of-an-integer-range-table).

Changing a table transform triggers a full table rewrite before the writer publishes the new layout. Configure encryption only under the schema-level `ducklake.encrypted` setting. The engine rejects table-level encryption, because DuckLake encryption is catalog scoped.

## Schema evolution

Before publication, the writer compares each incoming Parquet schema with the existing DuckLake table. It adds new nullable columns and applies only lossless type promotions.

It rejects removed columns, renames and incompatible type changes before changing the table. Inserts use column names, not column positions. Existing Parquet files aren't rewritten for these schema changes.

## Catalog lifecycle

The sync workers write the catalog as `ops` through the primary, and every PostgreSQL pod reads it as `duckdb` from its own instance. The Parquet files are at `s3://<bucket>/ducklake/<schema>/<table>/`. See [Serving layer](architecture/serving.md#ducklake-read-path).

## Partition fallback

Set `fallback: true` on a partitioned table to let its configured schema source serve data that DuckLake doesn't store. For a BigQuery schema, the table function reads published partitions from DuckLake and every other partition from BigQuery.

```json
{
  "name": "project.dataset.events",
  "strategy": "partitioned",
  "fallback": true
}
```

`fallback` is invalid on full tables. It's also invalid when the configured source has no fallback metadata. Without it, the table function reads DuckLake only. See [Proxy](proxy/index.md).

## Adding a source

A source is responsible for synchronization ingestion. It validates references, creates DuckDB scan expressions, reports modification and partition state, caches external clients and declares DuckDB extensions.

Register the source in `sync/src/sync/sources/registry.py`.

Each PostgreSQL schema has exactly one source. Add `source.settings` only when a source requires non-secret settings. Put credentials in environment variables or Kubernetes Secrets, not in `sync.json`.

DuckLake is the fixed local primary source. The configured schema source is the only possible partition fallback.

## Partitions

A `full` table creates one dump task. A partitioned table creates one extraction task for each batch of contiguous changed partitions. Each batch writes one Parquet file. See [Dump batches](operations/keda.md#dump-batches).

| Source type       | `n`       | `__NULL__`                        |
| ----------------- | --------- | --------------------------------- |
| Time partitioned  | Supported | Ignored                           |
| Range partitioned | Rejected  | Synced as the remainder partition |

`__UNPARTITIONED__` is unsupported and stops planning.

## Schema initialization

The provision job creates configured schemas, `access_policy`, `access_log`, row policies and triggers. It doesn't create application data tables in PostgreSQL. The sync workflow creates the views.

Application table names are PostgreSQL views over DuckLake functions.

---

<!-- nav:start -->
---

[Previous: Console layer](architecture/console.md) | [Next: Using the API](./using.md) | [Home](./index.md)
<!-- nav:end -->
