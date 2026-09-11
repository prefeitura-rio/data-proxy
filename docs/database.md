# Database Schema

The init-db Job creates all PostgreSQL objects before the first sync run. The objects below are created once and are idempotent.

## Extensions

| Extension   | Purpose                                                                                              |
| ----------- | ---------------------------------------------------------------------------------------------------- |
| `postgis`   | Geometry column support for BigQuery `GEOGRAPHY` types.                                              |
| `pg_duckdb` | Embeds DuckDB inside PostgreSQL. The Publisher uses `read_parquet()` to load Parquet files from GCS. |

## Roles

| Role                     | Login | Purpose                                                                                                                         |
| ------------------------ | ----- | ------------------------------------------------------------------------------------------------------------------------------- |
| `anon`                   | No    | PostgREST role for unauthenticated requests. Has no table access.                                                               |
| `user`                   | No    | PostgREST role for authenticated requests. Has `SELECT` on application tables, subject to RLS.                                  |
| `authenticator`          | Yes   | PostgREST connects as this role. PostgREST switches to `anon` or `user` based on the JWT role claim.                            |
| `policy_writer_<schema>` | No    | Service account role for one schema. Has `SELECT`, `INSERT`, and `UPDATE` on `<schema>.access_policy` only. Cannot delete rows. |
| `backup`                 | No    | Backup role for the backup CronJob. Has `USAGE` on each application schema and `SELECT` on `access_policy`.                     |

## Schemas

| Schema          | Purpose                                                                                                                 |
| --------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `rls`           | Holds the `sync_status` enum type and the `pre_request()` function.                                                     |
| `<application>` | One schema per key in the sync configuration `schemas` map. Holds application tables, `freshness`, and `access_policy`. |

## Enum types

| Type          | Schema | Values               |
| ------------- | ------ | -------------------- |
| `sync_status` | `rls`  | `success`, `failure` |

## Functions

### `rls.pre_request()`

Called before every PostgREST request. Mirrors JWT claims into session variables. See [Security](security.md) for details.

### `<schema>.<table>_bq`

When fallback is enabled for a table, the Publisher creates this view. The view reads the BigQuery source through pg_duckdb. It has the same schema policy and RLS behavior as the local table. The nginx proxy queries the view only after an empty local `GET` response.

## Tables

### `<schema>.freshness`

Records the last publication state for each table.

| Column         | Type              | Description                                                               |
| -------------- | ----------------- | ------------------------------------------------------------------------- |
| `table`        | `text`            | Table name.                                                               |
| `strategy`     | `text`            | Sync strategy: `full` or `partitioned`.                                   |
| `partition`    | `text`            | Partition ID. Null for full tables.                                       |
| `updated_at`   | `timestamptz`     | Time of the last successful publication. Null when no data was published. |
| `attempted_at` | `timestamptz`     | Time of the latest synchronization attempt.                               |
| `status`       | `rls.sync_status` | Result of the latest attempt: `success` or `failure`.                     |

Unique constraint: `(table, strategy, partition)`.

### `<schema>.access_policy`

Records access grants for RLS. See [Security](security.md) for how grants work.

| Column       | Type      | Description                                                        |
| ------------ | --------- | ------------------------------------------------------------------ |
| `subject`    | `text`    | JWT identity claim value (for example, `preferred_username`).      |
| `is_admin`   | `boolean` | When true, grants access to all rows. Defaults to `false`.         |
| `is_enabled` | `boolean` | When false, the grant is inactive. Defaults to `true`.             |
| `unit_type`  | `text`    | Unit type for matching against RLS columns.                        |
| `unit_id`    | `text`    | Unit ID for matching against RLS columns.                          |
| `metadata`   | `jsonb`   | Holds `created_at` and `updated_at`. Can hold any additional keys. |

Unique constraint: `(subject, unit_type, unit_id)`.

## Triggers

| Trigger                             | Table                    | Function                                  | Timing                  |
| ----------------------------------- | ------------------------ | ----------------------------------------- | ----------------------- |
| `access_policy_metadata_timestamps` | `<schema>.access_policy` | `set_access_policy_metadata_timestamps()` | Before insert or update |

## Policies

All RLS policies are described in [Security](security.md). The list below is a structural reference only.

| Policy                 | Table                    | Role     | Scope                                       |
| ---------------------- | ------------------------ | -------- | ------------------------------------------- |
| `schema_scope`         | `<schema>.freshness`     | `user`   | Schema claim check.                         |
| `user_read`            | `<schema>.access_policy` | `user`   | Schema claim check.                         |
| `access_policy_scoped` | Application tables       | `user`   | Schema claim check plus access grant match. |
| `backup_read`          | `<schema>.access_policy` | `backup` | Unrestricted read for backup.               |

## S3 secret

The init-db Job creates a DuckDB secret for S3 access inside pg_duckdb. The secret uses the configured `GCS_KEY_ID` and `GCS_SECRET_KEY` credentials. This lets the Publisher call `read_parquet()` on GCS paths without per-query authentication.
