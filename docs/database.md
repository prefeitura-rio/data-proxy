# Database Schema

This page describes the PostgreSQL schemas, the roles, the application tables and the DuckLake storage. [Data model](model.md) has diagrams of the tables.

CloudNativePG (CNPG) creates and maintains the core roles. Init-db installs extensions, creates the internal `data_proxy` schema, creates configured application schemas and creates access policy metadata.

## Schemas

A schema is an API that PostgREST serves, a rule that the APIs share, or private. The application database `data-proxy` contains:

| Schema | Kind | Contains |
| --- | --- | --- |
| `rbac` | Shared rule | The functions `load()`, `can()` and `mine()` and the table `roles`, which the migrations create and the `configure-db` job fills from `rbac.toml`. Every other schema calls it. PostgREST doesn't serve it. |
| `console` | API, served | The views, the functions, and the three configuration tables that the web app uses. |
| `data_proxy` | Private | The `state`, `errors`, and `scope` tables of the engine, the routing and reload functions, `sees()` (which workflows a token sees), and the maintenance procedures. |
| `dbos` | Private, owned by DBOS | The workflow tables. |
| `access` | API, served | The table `policy`, which stores the grants of every project, and the table `log`, which records each change. A scope of one `ltree` column says what a grant covers. |
| One schema for each source schema | API, served | The views and the functions of the synced tables. |
| `public`, `duckdb` | Defaults | The goose version table, the extensions, and the objects of `pg_duckdb`. |

A source schema has a physical name, which is `<project>__<short name>`, also in the project `default`. The name always has two underscores, so it never equals the name of a schema of the system. The S3 folder of its files is the short name in `default`, and `<project>_<short name>` in another project. A check on `console.schemas` refuses these names.

The `ducklake` database contains one metadata schema for each source schema, with the same name. It's a separate database because the catalog has its own owner, `ops`, and its own read-only login, `duckdb`.

DBOS stays in the application database. The console views join `dbos.workflow_status` with `data_proxy.scope` and `data_proxy.state`, and PostgreSQL can't join across databases. `console.start_sync()` and the `reload_proxy` trigger enqueue a workflow with `dbos.enqueue_workflow()`, and the trigger does it in the same transaction as the edit. A second database in the same cluster would share the write-ahead log and the disks, so it would isolate nothing. If the workflow load ever hurts the serving layer, move DBOS to its own CNPG cluster. Then let the console read workflow state through a second API.

## Shared objects

| Object | Purpose |
| --- | --- |
| `postgis` | BigQuery `GEOGRAPHY` support. |
| `pg_duckdb` | Executes DuckDB scans and reads SeaweedFS Parquet. |
| `ltree` | The paths of projects, schemas and tables in the view `console.catalog`. |

The function `data_proxy.pre_request()` isn't an extension. It mirrors the JSON Web Token (JWT) claims into PostgreSQL session settings.

## Application schema

Each configured schema contains metadata and views:

| Object | Purpose |
| --- | --- |
| `<schema>.<table>` | PostgreSQL view over the table function. |
| `<schema>.<table>_fn()` | `SECURITY DEFINER` function that checks row-level security (RLS), plans the sources, and reads them. |
| `<schema>.agg("table")` | `SECURITY DEFINER` wrapper that runs the aggregate of a request inside DuckDB for the table in the argument. It answers `null` for a request that the table function must serve. |
| `<schema>.changes("table", bigint, bigint)` | `SECURITY DEFINER` wrapper that returns the DuckLake change feed of the table, with the same schema scope and row policy. The row is in the column `data`. |
| `<schema>.hll("table", "column", group_by, lg_k)` | `SECURITY DEFINER` wrapper that estimates the distinct values of a column with a HyperLogLog sketch. |
| `<schema>.<table>_dl_fn(text, text, boolean, text[], text)` | Private `SECURITY DEFINER` helper that reads DuckLake at one snapshot, with the columns and the tail that the request needs. |
| `<schema>.<table>_bq_fn(text, text)` | Private `SECURITY DEFINER` helper that reads the schema source (BigQuery today) when a partitioned table sets `fallback: true`. |

One function, `rls_where`, builds the row policy for the table function and for the three wrappers, so the view and the aggregates never read different rows for one user. The three wrappers call functions of the application schema with the name of the schema. The functions read the columns, the row rules, the claim and the scope of the table from `data_proxy.meta`, which the sync writes with the view. The wrappers exist once for each schema, and no function exists for each table, except `<table>_fn` and the helpers.

Internal functions and procedures live in the application schema (`data_proxy` by default): `meta`, `agg`, `hll`, `changes`, `request_filter`, `render_ducklake_where`, `requested_snapshot`, source routing, access-log triggers and maintenance procedures. The schema isn't in `PGRST_DB_SCHEMAS`, so these objects aren't exposed as PostgREST RPC endpoints. See [Proxy](proxy/index.md).

PostgreSQL doesn't contain materialized application data tables. The table views call DuckDB, which reads the catalog of its own PostgreSQL instance and scans Parquet in SeaweedFS.

## DuckLake storage

DuckLake uses one catalog per configured schema, in the `ducklake` database of the CNPG cluster:

```text
database ducklake, schema <schema>   # DuckLake catalog metadata
s3://<bucket>/ducklake/<schema>/     # DuckLake Parquet data
```

DBOS sync workers write the catalogs as the `ops` role, through the primary. Streaming replication copies them to every standby. pg_duckdb opens the catalog of its own instance read-only, as the `duckdb` role. See [Serving layer](architecture/serving.md#ducklake-read-path).

The catalog contains DuckLake metadata, not PostgreSQL application rows. DuckLake table configuration uses `SET PARTITIONED BY` and `SET SORTED BY` for partition and RLS-related columns.

[Database roles and grants](grants.md) describes the roles, the security and the pushdown of the predicates and the S3 access.
