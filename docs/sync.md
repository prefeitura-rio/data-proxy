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

| Field | Required | Meaning |
| --- | --- | --- |
| `claim` | When a table uses `rls` | JWT claim matched against `access_policy.subject`. |
| `tables` | No | Tables in this PostgreSQL schema. Defaults to `[]`. |

The schema key is the target PostgreSQL schema. Do not add a schema field to a table entry.

## Table fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | Yes | BigQuery reference: `project.dataset.table`. |
| `strategy` | Yes | `full` replaces the DuckLake table; `partitioned` updates physical partitions. |
| `n` | No | Keep the newest `n` time partitions. |
| `fallback` | No | Enables the `_bq` fallback view. Default: `true`. |
| `cache_ttl` | No | Fallback cache lifetime in seconds. |
| `retention` | No | `{ "column": "created_at", "window": "365 days" }`. |
| `rls` | No | Unit column and unit type pairs. See [Security](security.md). |
| `indexes` | No | DuckLake sort columns. These are rendered as `SET SORTED BY`, not PostgreSQL indexes. |

## Pipeline

The scheduled `run_sync` workflow performs these steps:

1. Build the changed-table and changed-partition plan.
2. Enqueue BigQuery dump tasks.
3. Write independent scratch Parquet files to the S3 scratch prefix. Extraction does not merge files.
4. Run `seed_schemas` in parallel with publication. Seed reconciles only PostgreSQL functions and views.
5. Enqueue one `publish_schema` workflow per schema.
6. DBOS sync workers insert scratch Parquet into DuckLake and commit the writer catalog volume.
7. The `data-proxy-litestream` sidecar replicates writer catalog WAL changes to SeaweedFS.
8. The `data-proxy-litestream` restore container updates the reader catalog volume.
9. Expire old DuckLake snapshots and clean unreferenced data files.
10. Flush scratch objects and Valkey.

Publishing is parallel across schemas and sequential within a schema. There is one active writer for each schema catalog.

PostgREST rolls out only if seed adds or removes a view. A new Parquet snapshot does not change the PostgreSQL view set, so it does not trigger a rollout.

## Catalog lifecycle

Each schema uses these locations:

```text
local writer:    /var/lib/ducklake/catalogs/<schema>/catalog.sqlite
Litestream S3:   s3://<bucket>/ducklake/<schema>/catalog.sqlite/ (LTX replica prefix)
DuckLake data:   s3://<bucket>/ducklake-data/<schema>/
```

The DBOS sync writes the writer catalog volume. The Litestream replicate sidecar reads that volume. The restore container writes the separate reader catalog volume. CNPG PostgreSQL instances mount the reader volume read-only. PgBouncer uses session pooling so a DuckLake attachment can remain on a backend session, but the attachment is refreshed when the catalog revision changes.

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

## Batching and partitions

A `full` table creates one dump task. Changed physical partitions are grouped into dump tasks. Each task writes one Parquet file.

```yaml
sync:
  dumper:
    batchMegaBytes: 600
    batchMaxPartitions: 256
```

`batchMegaBytes` is the uncompressed source-data target in MiB. `batchMaxPartitions` limits the number of partitions in one task.

| Source type | `n` | `__NULL__` |
| --- | --- | --- |
| Time partitioned | Supported | Ignored |
| Range partitioned | Rejected | Synced as the remainder partition |

`__UNPARTITIONED__` is unsupported and stops planning.

## Schema initialization

The sync workflow creates configured schemas, `freshness`, `access_policy`, `access_log`, policies, triggers, and `policy_writer_<schema>` roles. It does not create application data tables in PostgreSQL. Application table names are PostgreSQL views over DuckLake functions.

## Retention

Set `retention` on a table to delete rows older than a time window. Enable the retention job with `retention.enabled: true`. Retention is operational cleanup and does not trigger a resync.

---

[← Previous](architecture.md) · [Home](../README.md) · [Next →](using.md)
