# Database Schema

Chart initialization creates shared roles, extensions, and the `rls` schema. The sync workflow creates configured application schemas, policy objects, and tables.

## Shared objects

| Object              | Purpose                                           |
| ------------------- | ------------------------------------------------- |
| `postgis`           | BigQuery `GEOGRAPHY` support.                     |
| `pg_duckdb`         | Reads Parquet files from the S3-compatible store. |
| `rls.sync_status`   | `success` and `failure` freshness status.         |
| `rls.pre_request()` | Mirrors JWT claims into session variables.        |

## Roles

| Role                     | Purpose                                                             |
| ------------------------ | ------------------------------------------------------------------- |
| `anon`                   | No application table access.                                        |
| `user`                   | Read access subject to schema and row conditions.                   |
| `authenticator`          | PostgREST login role.                                               |
| `policy_writer_<schema>` | Reads and writes one schema's `access_policy` table. Cannot delete. |
| `backup`                 | Reads `access_policy` for backups.                                  |

## Application schema

Each configured schema contains:

| Object                   | Purpose                                           |
| ------------------------ | ------------------------------------------------- |
| `<schema>.freshness`     | Latest publication status by table and partition. |
| `<schema>.access_policy` | Access grants.                                    |
| Synced tables            | Local PostgreSQL copies of BigQuery tables.       |
| `<table>_bq`             | Fallback view when enabled.                       |

`freshness` has one row for a full table and one row per known partition for a partitioned table.

`access_policy` has a unique key on `(subject, unit_type, unit_id)`. Its metadata trigger manages `created_at` and `updated_at`.

## Policies and S3 access

The sync workflow creates schema and row conditions for application tables. See [Security](security.md) for access behavior.

The chart creates a pg_duckdb S3 secret from `S3_ACCESS_KEY` and `S3_SECRET_KEY`. The Publisher uses it to read Parquet files with `read_parquet()`.

---

[← Previous](keda.md) · [Home](../README.md) · [Next →](environment_variables.md)
