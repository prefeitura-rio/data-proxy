# Sync

The sync configuration is a JSON file. Set `SYNC_CONFIG_PATH` to its location.

```json
{
  "schemas": {
    "my_schema": {
      "claim": "preferred_username",
      "tables": [
        {
          "name": "project.dataset.table",
          "strategy": "full",
          "rls": [{ "column": "unit_id", "unit_type": "unit" }]
        },
        {
          "name": "project.dataset.people",
          "strategy": "partitioned"
        },
        {
          "name": "project.dataset.events",
          "strategy": "partitioned",
          "n": 7,
          "indexes": [{ "name": "idx_events_unit", "columns": ["unit_id"] }],
          "rls": [{ "column": "unit_id", "unit_type": "unit" }]
        },
        {
          "name": "project.dataset.participants",
          "strategy": "full",
          "rls": [
            { "column": "id_cras", "unit_type": "cras" },
            { "column": "id_escola", "unit_type": "escola" }
          ]
        }
      ]
    }
  }
}
```

The top-level `schemas` map is the single source of truth for which PostgreSQL schemas exist. Each key is a schema name; its value declares the schema's identity claim (if any of its tables use `rls`) and the list of tables that land in it. A table's schema is never a field of its own -- it is exactly the key it is nested under.

| Field    | Required | Description                                                                                                                                                        |
| -------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `claim`  | RLS only | JWT claim identifying the requester for every table in this schema, compared against `access_policy.subject`. Required if any table in this schema declares `rls`. |
| `tables` | no       | Array of table entries landing in this schema. Defaults to an empty list.                                                                                          |

Each entry in `tables` accepts:

| Field      | Required | Description                                                                                                                                                                                         |
| ---------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`     | yes      | Full BigQuery table reference (`project.dataset.table`).                                                                                                                                            |
| `strategy` | yes      | `full` replaces the whole table. `partitioned` syncs one physical partition at a time.                                                                                                              |
| `n`        | no       | Keep only the last `n` partitions. Time-partitioned tables only.                                                                                                                                    |
| `rls`      | no       | Array of `{ column, unit_type }` pairs. A row is visible if any pair matches a grant in `rls.access_policy` for the requester. Omit `rls` to disable RLS on the table. See [Security](security.md). |
| `indexes`  | no       | Array of `{ name, columns }` objects. Creates one index per entry after each sync.                                                                                                                  |

## Schema Creation

You never run `CREATE SCHEMA` yourself. Every finalizer run creates every schema declared in the top-level `schemas` map, if it does not already exist, before publishing any table:

- The schema itself (`CREATE SCHEMA IF NOT EXISTS`).
- `GRANT USAGE` on it to the `user` role, so PostgREST can reach tables inside it.
- Its `policy_writer_<schema>` role (see [Security](security.md#row-level-security-rls)), whether or not any table in that schema declares `rls`.

This means adding a new entry to the top-level `schemas` map is enough on its own — the next sync run creates and grants it, with no separate migration step.

## Strategy Selection

| Strategy      | Use when                                                                                                                                          |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `full`        | The complete table fits comfortably in one extraction task.                                                                                       |
| `partitioned` | The source table is time or range-partitioned in BigQuery and should sync one physical partition at a time, optionally keeping only the last `n`. |

`partitioned` reads the partition column, type (time or range), bounds/interval, and existing partition IDs directly from BigQuery metadata. Do not add these values to the sync configuration. Each new or changed physical partition becomes one worker task and one Parquet file; only partitions whose BigQuery metadata changed are re-extracted. Removed partitions are deleted during finalization, and publication remains atomic.

For time-partitioned tables, `n` keeps only the highest `n` raw partition ids (BigQuery's `partition_id`, e.g. `20250115` for a daily partition) and drops older partitions incrementally as new ones appear. The partition id is used exactly as BigQuery reports it -- there is no granularity conversion (daily/monthly/yearly). If a table needs a different granularity, model that in BigQuery or in a derived dataset, not in this configuration. `n` is rejected for range-partitioned tables, since a range partition has no natural recency ordering. BigQuery's `__NULL__` bucket is skipped for time-partitioned tables (a null time value carries no partition identity) and is synced as a remainder partition for range-partitioned tables (it holds real out-of-range or null data). `__UNPARTITIONED__` always raises, since it indicates an unsupported partitioning type.
