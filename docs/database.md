# Database Schema

See [Data model](model.md) for diagrams of the tables.

CloudNativePG (CNPG) creates and maintains the core roles. Init-db installs extensions, creates the internal `data_proxy` schema, creates configured application schemas, and creates access policy metadata.

## Schemas

A schema is an API that PostgREST serves, a rule that the APIs share, or private. The application database `data-proxy` contains:

| Schema | Kind | Contains |
| --- | --- | --- |
| `rbac` | Shared rule | The table `roles` that the `configure-db` job fills from `rbac.toml`, and the functions `load()`, `can()` and `mine()`. Every other schema calls it. PostgREST doesn't serve it. |
| `console` | API, served | The views, the functions, and the three configuration tables that the web app uses. |
| `data_proxy` | Private | The `state`, `errors`, and `scope` tables of the engine, the routing and reload functions, `sees()` (which workflows a token sees), and the maintenance procedures. |
| `dbos` | Private, owned by DBOS | The workflow tables. |
| One schema for each source schema | API, served | `access_policy`, `access_log`, and the views and functions of the synced tables. |
| `public`, `duckdb` | Defaults | The goose version table, the extensions, and the objects of `pg_duckdb`. |

A source schema can't have the name `console`, `rbac`, `data_proxy`, `dbos`, `public`, `duckdb`, or `information_schema`, and its name can't start with `pg_`. A check on `console.schemas` refuses these names.

The `ducklake` database contains one metadata schema for each source schema, with the same name. It's a separate database because the catalog has its own owner, `ops`, and its own read-only login, `duckdb`.

DBOS stays in the application database. The console views join `dbos.workflow_status` with `data_proxy.scope` and `data_proxy.state`, and PostgreSQL can't join across databases. `console.start_sync()` and the `reload_proxy` trigger enqueue a workflow with `dbos.enqueue_workflow()`, and the trigger does it in the same transaction as the edit. A second database in the same cluster would share the write-ahead log and the disks, so it would isolate nothing. If the workflow load ever hurts the serving layer, move DBOS to its own CNPG cluster. Then let the console read workflow state through a second API.

## Shared objects

| Object | Purpose |
| --- | --- |
| `postgis` | BigQuery `GEOGRAPHY` support. |
| `pg_duckdb` | Executes DuckDB scans and reads SeaweedFS Parquet. |

The function `data_proxy.pre_request()` isn't an extension. It mirrors the JSON Web Token (JWT) claims into PostgreSQL session settings.

## Application schema

Each configured schema contains metadata and views:

| Object | Purpose |
| --- | --- |
| `<schema>.access_policy` | Active access grants. |
| `<schema>.access_log` | Append-only audit trail of grant changes. |
| `<schema>.<table>` | PostgreSQL view over the table function. |
| `<schema>.<table>_fn()` | `SECURITY DEFINER` function that checks row-level security (RLS), plans the sources, and reads them. |
| `<schema>.<table>_aggregate()` | `SECURITY DEFINER` function that applies RLS and runs the aggregate of a request inside DuckDB. It answers `null` for a request that the table function must serve. A table with a name longer than 53 bytes has none. |
| `<schema>.<table>_dl_fn(text, text, boolean, text[], text)` | Private `SECURITY DEFINER` helper that reads DuckLake at one snapshot, with the columns and the tail that the request needs. |
| `<schema>.<table>_bq_fn(text, text)` | Private `SECURITY DEFINER` helper that reads the schema source (BigQuery today) when a partitioned table sets `fallback: true`. |

Internal functions and procedures live in the application schema (`data_proxy` by default): `request_filter`, `render_ducklake_where`, `requested_snapshot`, source routing, access-log triggers, and maintenance procedures. The schema isn't in `PGRST_DB_SCHEMAS`, so these objects aren't exposed as PostgREST RPC endpoints. See [Proxy](proxy/index.md).

PostgreSQL doesn't contain materialized application data tables. The table views call DuckDB, which reads the catalog of its own PostgreSQL instance and scans Parquet in SeaweedFS.

## DuckLake storage

DuckLake uses one catalog per configured schema, in the `ducklake` database of the CNPG cluster:

```text
database ducklake, schema <schema>   # DuckLake catalog metadata
s3://<bucket>/ducklake/<schema>/     # DuckLake Parquet data
```

DBOS sync workers write the catalogs as the `ops` role, through the primary. Streaming replication copies them to every standby. pg_duckdb opens the catalog of its own instance read-only, as the `duckdb` role. See [Serving layer](architecture/serving.md#ducklake-read-path).

The catalog contains DuckLake metadata, not PostgreSQL application rows. DuckLake table configuration uses `SET PARTITIONED BY` and `SET SORTED BY` for partition and RLS-related columns.

[Database roles and grants](grants.md) describes the roles, the security and the pushdown of the predicates, and the S3 access.

<!-- nav:start -->
---

[Previous: Table settings](proxy/reload.md) | [Next: Database roles and grants](grants.md) | [Home](index.md)
<!-- nav:end -->
