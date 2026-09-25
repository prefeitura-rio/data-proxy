# Database Schema

CNPG creates and maintains the core roles. Init-db installs extensions, creates the `rls` schema, creates configured application schemas, and creates access policy metadata.

## Shared objects

| Object | Purpose |
| --- | --- |
| `postgis` | BigQuery `GEOGRAPHY` support. |
| `pg_duckdb` | Executes DuckDB scans and reads SeaweedFS Parquet. |

| `rls.pre_request()` | Mirrors JWT claims into PostgreSQL session settings. |

## Roles

| Role | Purpose |
| --- | --- |
| `anon` | Unauthenticated PostgREST role with no application data access. |
| `user` | Authenticated read role subject to schema and row conditions. |
| `authenticator` | PostgREST login role. CNPG keeps it `NOINHERIT` and grants membership in `anon` and `user`. |
| `policy_writer_<schema>` | Reads and writes one schema's `access_policy` table. |
| `jobs` | Maintenance role for backup, cleanup, and retention jobs. |

## Application schema

Each configured schema contains metadata and views:

| Object | Purpose |
| --- | --- |
| `<schema>.access_policy` | Active access grants. |
| `<schema>.access_log` | Append-only audit trail of grant changes. |
| `<schema>.<table>` | PostgreSQL view over the DuckLake-backed function. |
| `<schema>.<table>_bq` | BigQuery-backed fallback view when enabled. |
| `<schema>.<table>_fn()` | `SECURITY DEFINER` DuckLake function with manual RLS filtering. |
| `<schema>.<table>_bq_fn()` | `SECURITY DEFINER` BigQuery function with the same RLS filtering. |

PostgreSQL does not contain materialized application data tables. The table views call DuckDB, which reads the local restored SQLite catalog and scans Parquet in SeaweedFS.


## DuckLake storage

DuckLake uses one SQLite catalog per configured schema:

```text
s3://<bucket>/ducklake/<schema>/catalog.sqlite/  # Litestream LTX replica prefix
s3://<bucket>/ducklake-data/<schema>/      # DuckLake Parquet data
```

DBOS sync workers update the writer catalog volume. The `data-proxy-litestream` replication sidecar replicates committed WAL changes to SeaweedFS, and its restore container updates the separate reader catalog volume. CNPG instances mount the reader catalogs read-only. pg_duckdb opens those local catalogs read-only.

The catalog contains DuckLake metadata, not PostgreSQL application rows. DuckLake table configuration uses `SET PARTITIONED BY` and `SET SORTED BY` for partition and RLS-related columns.

## Security and predicate pushdown

The PostgreSQL functions are `SECURITY DEFINER` and read `access_policy` using the request claim settings. They build the allowed-row predicate and pass it into the DuckDB SQL query. This enforces RLS before the Parquet scan returns rows.

The BigQuery fallback functions use the same policy-generation logic. See [Security](security.md).

## S3 access

The chart creates an S3 secret from `S3_ACCESS_KEY` and `S3_SECRET_KEY`. The `data-proxy-litestream` containers use these credentials for catalog replication and restore. pg_duckdb uses the configured DuckDB S3 secret to read DuckLake Parquet.

Production GCP credentials are projected into every CNPG instance through `gcp.existingSecret` because BigQuery fallback queries can run on read replicas.

---

[← Previous](keda.md) · [Home](../README.md) · [Next →](environment_variables.md)
