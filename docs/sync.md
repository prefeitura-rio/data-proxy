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

The top-level `schemas` map is the single source of truth for which PostgreSQL schemas exist. Each key is a schema name. Each value declares the schema's identity claim, needed only if any of its tables use `rls`. Each value also declares the list of tables that land in that schema. A table has no separate schema field. A table's schema is the key it sits under.

| Field    | Required | Description                                                                                                                                                                                  |
| -------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `claim`  | RLS only | JWT claim that identifies the requester for every table in this schema. Data Proxy compares this claim against `access_policy.subject`. Required if any table in this schema declares `rls`. |
| `tables` | no       | Array of table entries that land in this schema. Defaults to an empty list.                                                                                                                  |

Each entry in `tables` accepts:

| Field      | Required | Description                                                                                                                                                                                             |
| ---------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`     | yes      | Full BigQuery table reference (`project.dataset.table`).                                                                                                                                                |
| `strategy` | yes      | `full` replaces the whole table. `partitioned` syncs one physical partition at a time.                                                                                                                  |
| `n`        | no       | Keep only the last `n` partitions. Applies to time-partitioned tables only.                                                                                                                             |
| `rls`      | no       | Array of `{ column, unit_type }` pairs. A row is visible when any pair matches a grant in `rls.access_policy` for the requester. Omit `rls` to turn off RLS on this table. See [Security](security.md). |
| `indexes`  | no       | Array of `{ name, columns }` objects. Data Proxy creates one index per entry after each sync.                                                                                                           |

## Geometry Columns

Data Proxy keeps a Parquet `GEOMETRY` column as a PostgreSQL `geometry` column. The database image must contain PostGIS. Database initialization enables the `postgis` extension before publication.

## Schema Creation

You never run `CREATE SCHEMA` yourself. Every finalizer run checks every schema declared in the top-level `schemas` map. For each schema that does not yet exist, the finalizer creates it before it publishes any table. The finalizer creates:

- The schema itself (`CREATE SCHEMA IF NOT EXISTS`).
- A `GRANT USAGE` on the schema, for the `user` role. This grant lets PostgREST reach tables inside the schema.
- The schema's `policy_writer_<schema>` role (see [Security](security.md#row-level-security-rls)). The finalizer creates this role even when no table in the schema declares `rls`.

Adding a new entry to the top-level `schemas` map needs no separate migration step. The next sync run creates the schema and grants it.

## Strategy Selection

| Strategy      | Use when                                                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `full`        | The complete table fits comfortably in one extraction task.                                                                          |
| `partitioned` | The source table is time or range-partitioned in BigQuery. Sync one physical partition at a time. Optionally keep only the last `n`. |

The `partitioned` strategy reads several facts directly from BigQuery metadata. These facts are the partition column, the partition type (time or range), the bounds or interval, and the existing partition IDs. Do not add these values to the sync configuration. Each new or changed physical partition becomes one worker task and one Parquet file. The producer re-extracts only the partitions whose BigQuery metadata changed. Finalization deletes removed partitions. Publication stays atomic through this whole process.

For time-partitioned tables, `n` keeps only the highest `n` raw partition ids. BigQuery calls this id `partition_id` (for example `20250115` for a daily partition). As new partitions appear, older partitions drop off incrementally. Data Proxy uses the partition id exactly as BigQuery reports it. Data Proxy applies no granularity conversion, for example from daily to monthly. If a table needs a different granularity, model that granularity in BigQuery or in a derived dataset. Do not model it in this configuration. `n` is rejected for range-partitioned tables. A range partition has no natural recency order to rank by. BigQuery's `__NULL__` bucket behaves differently by partition type. For time-partitioned tables, Data Proxy skips this bucket. A null time value carries no partition identity. For range-partitioned tables, Data Proxy syncs this bucket as a remainder partition. This partition holds real out-of-range or null data. `__UNPARTITIONED__` always raises an error. This value indicates an unsupported partitioning type.
