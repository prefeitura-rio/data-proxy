# Database Schema

CNPG creates and maintains the core roles. Init-db installs extensions, creates the internal `data_proxy` schema, creates configured application schemas, and creates access policy metadata.

## Shared objects

| Object | Purpose |
| --- | --- |
| `postgis` | BigQuery `GEOGRAPHY` support. |
| `pg_duckdb` | Executes DuckDB scans and reads SeaweedFS Parquet. |

| `data_proxy.pre_request()` | Mirrors JWT claims into PostgreSQL session settings. |

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
| `<schema>.<table>` | PostgreSQL view over the table function. |
| `<schema>.<table>_fn()` | `SECURITY DEFINER` function that checks RLS, plans the sources, and reads them. |
| `<schema>.<table>_dl_fn(text, text)` | Private `SECURITY DEFINER` helper that reads DuckLake at one snapshot. |
| `<schema>.<table>_bq_fn(text, text)` | Private `SECURITY DEFINER` helper that reads BigQuery when a partitioned BigQuery table sets `fallback: true`. |

Internal functions and procedures live in the application schema (`data_proxy` by default): `request_filter`, `render_ducklake_where`, `requested_snapshot`, source routing, access-log triggers, and maintenance procedures. The schema isn't in `PGRST_DB_SCHEMAS`, so these objects aren't exposed as PostgREST RPC endpoints. See [Proxy](proxy.md).

PostgreSQL doesn't contain materialized application data tables. The table views call DuckDB, which reads the catalog of its own PostgreSQL instance and scans Parquet in SeaweedFS.


## DuckLake storage

DuckLake uses one catalog per configured schema, in the `ducklake` database of the CNPG cluster:

```text
database ducklake, schema <schema>   # DuckLake catalog metadata
s3://<bucket>/ducklake/<schema>/     # DuckLake Parquet data
```

DBOS sync workers write the catalogs as the `ducklake_writer` role, through the primary. Streaming replication copies them to every standby. pg_duckdb opens the catalog of its own instance read-only, as the `ducklake_reader` role. See [Architecture](architecture.md#catalog-database).

The catalog contains DuckLake metadata, not PostgreSQL application rows. DuckLake table configuration uses `SET PARTITIONED BY` and `SET SORTED BY` for partition and RLS-related columns.

## Security and predicate pushdown

The PostgreSQL functions are `SECURITY DEFINER` and read `access_policy` using the request claim settings. They build the allowed-row predicate and pass it into the DuckDB SQL query. This enforces RLS before the Parquet scan returns rows.

The table function builds the policy predicate once and passes it to each source helper. See [Security](security.md).

## S3 access

The chart creates an S3 secret from `S3_ACCESS_KEY` and `S3_SECRET_KEY`. pg_duckdb uses the configured DuckDB S3 secret to read DuckLake Parquet.

Production GCP credentials are projected into every CNPG instance through `gcp.existingSecret` because BigQuery fallback queries can run on read replicas.

---

[← Previous](keda.md) · [Home](../README.md) · [Next →](environment_variables.md)
