# Database Schema

CNPG creates and maintains the core roles. Init-db creates extensions, the `rls` schema, configured application schemas, policy objects, and tables.

## Shared objects

| Object              | Purpose                                           |
| ------------------- | ------------------------------------------------- |
| `postgis`           | BigQuery `GEOGRAPHY` support.                     |
| `pg_duckdb`         | Reads Parquet files from the S3-compatible store. |
| `rls.sync_status`   | `success` and `error` freshness status.         |
| `rls.pre_request()` | Mirrors JWT claims into session variables.        |

## Roles

| Role                     | Purpose                                                             |
| ------------------------ | ------------------------------------------------------------------- |
| `anon`                   | No application table access.                                        |
| `user`                   | Read access subject to schema and row conditions.                   |
| `authenticator`          | PostgREST login role. CNPG keeps it `NOINHERIT` and grants membership in `anon` and `user`. |
| `policy_writer_<schema>` | Reads and writes one schema's `access_policy` table. Created by init-db/Publisher. Deleting a row revokes the grant. |
| `jobs`                   | Shared maintenance role for the backup and cleanup CronJobs. It reads and prunes the governance tables, and drops stale tables through a `SECURITY DEFINER` helper. |

## Application schema

Each configured schema contains:

| Object                   | Purpose                                           |
| ------------------------ | ------------------------------------------------- |
| `<schema>.freshness`     | Latest publication status by table and partition. |
| `<schema>.access_policy` | Active access grants.                             |
| `<schema>.access_log`    | Append-only audit trail of every grant change.    |
| Synced tables            | Local PostgreSQL copies of BigQuery tables.       |
| `<table>_bq`             | Fallback view when enabled.                       |

`freshness` has one row for a full table and one row per known partition for a partitioned table.

`access_policy` holds only active grants. It has a unique key on `(subject, unit_type, unit_id)` covering `is_admin`, and its metadata trigger manages `created_at` and `updated_at`. Revoking access deletes the row.

`access_log` records every insert, update, and delete with the previous row state. Its trigger runs as the defining role so low-privilege writers are logged. The backup CronJob keeps `access_log` for `backup.accessLog.retentionDays` days.

The cleanup CronJob runs as the `jobs` role, not as the database owner. It drops stale tables through `<schema>.drop_table_if_exists()`, a `SECURITY DEFINER` function owned by the schema owner.

Application CNPG clusters set `duckdb.max_memory` from `cnpg.duckdb.maxMemory`. Keep this cap below the pod memory limit.

## Policies and S3 access

The sync workflow creates schema and row conditions for application tables. See [Security](security.md) for access behavior.

The chart creates a pg_duckdb S3 secret from `S3_ACCESS_KEY` and `S3_SECRET_KEY`. The Publisher uses it to read Parquet files with `read_parquet()`.

Production GCP credentials are projected into every CNPG instance through `gcp.existingSecret`. This is required because pg_duckdb fallback queries can run on read replicas.

---

[← Previous](keda.md) · [Home](../README.md) · [Next →](environment_variables.md)
