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

| Field       | Required | Meaning                                                                                                                                         |
| ----------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`      | Yes      | BigQuery reference: `project.dataset.table`.                                                                                                    |
| `strategy`  | Yes      | `full` replaces the table; `partitioned` updates physical partitions.                                                                           |
| `n`         | No       | Keep the newest `n` time partitions.                                                                                                            |
| `fallback`  | No       | Enables the `_bq` fallback view for this table when chart fallback is enabled. Set `false` to disable fallback for this table. Default: `true`. |
| `cache_ttl` | No       | Fallback cache lifetime in seconds.                                                                                                             |
| `rls`       | No       | Unit column and unit type pairs. See [Security](security.md).                                                                                   |
| `indexes`   | No       | Index definitions created after publication.                                                                                                    |
| `fallback`  | No       | Enable BigQuery fallback for the table                                                                                                          |

`indexes.expressions` contains raw SQL. Use it only in trusted configuration. Expressions are inserted into `CREATE INDEX` DDL.

## Fallback per table

Fallback has two gates. The chart-level fallback configuration must be enabled, and the table must have `fallback: true` (the default). Use `fallback: false` for tables that must never query BigQuery when the local table is empty.

```json
{
  "name": "project.dataset.internal_table",
  "strategy": "full",
  "fallback": false
}
```

When fallback is disabled for a table, the Publisher does not create its `_bq` view and nginx returns the local PostgREST response without a BigQuery fallback request. See [Fallback](fallback.md).

## Batching

A `full` table creates one Dumper task. Changed physical partitions are grouped into Dumper tasks. Each task writes one Parquet file.

```yaml
dumper:
  batchMegaBytes: 600
  batchMaxPartitions: 256
```

`batchMegaBytes` is the uncompressed source-data target in MiB. `batchMaxPartitions` limits the number of partitions in one task. A batch closes before adding a partition that would exceed either limit.

## Partition behavior

| Source type       | `n`       | `__NULL__`                         |
| ----------------- | --------- | ---------------------------------- |
| Time partitioned  | Supported | Ignored.                           |
| Range partitioned | Rejected  | Synced as the remainder partition. |

`__UNPARTITIONED__` is unsupported and stops planning.

## Schema initialization

The sync workflow creates configured schemas, `access_policy` tables, `access_log` audit tables, policies, triggers, and `policy_writer_<schema>` roles when needed. Do not create them manually.

## JSON and geometry

BigQuery `STRUCT` and `RECORD` columns are extracted as JSON, loaded as PostgreSQL `json`, and converted to `jsonb` in the publication transaction. Use a GIN expression index for JSON paths.

```json
{
  "name": "idx_status",
  "columns": ["indicadores"],
  "method": "gin",
  "expressions": ["(indicadores->'status')"]
}
```

BigQuery `GEOGRAPHY` columns map to PostgreSQL `geometry`. The database image enables PostGIS.

---

[← Previous](architecture.md) · [Home](../README.md) · [Next →](using.md)
