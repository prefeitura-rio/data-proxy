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

## Roles

| Role | Purpose |
| --- | --- |
| `anon` | The role of a request without a token. It has no access to application data. |
| `dp_api`, `dp_policy`, `dp_console`, `dp_admin` | One role for each role of `rbac.toml`. A token switches to the role of its `role` claim. The last three roles are members of `dp_api` and share its grants. `rbac.can()` and `rbac.mine()` decide what each role may do. |
| `serving` | The login of the serving layer (`auth.servingRole`). PostgREST, the PgDog pool and the `recovery` Deployment connect with it. It's `NOINHERIT` and a member of `anon` and of every `dp_*` role. It has no rights of its own and only switches into the request role of the token. `configure-db` creates it with the password of its Secret. No token can switch to it. |
| `ops` | The owner of both databases (`cnpg.db.user`). It's the login of every internal workload: the sync workers, the recovery deployment, the chart jobs, the backup, and the autoscaler. It's a superuser. pg_duckdb turns the local file system off for every other role, and DuckDB loads its extensions and writes its persistent secrets there. It owns the `SECURITY DEFINER` functions that run DuckDB. No token can switch to it. |
| `duckdb` | The read-only login of the catalog database. pg_duckdb uses it to read the metadata schemas. |

`configure-db` creates `anon`, the `dp_*` roles and `serving` as `ops`, so a new role in `rbac.toml` needs no chart change.

The Secret of a login has the name `<release>-cnpg-<role>`: `-cnpg-ops`, `-cnpg-serving`, and `-cnpg-duckdb`. After an upgrade of an install that used the old names, the chart creates the new Secrets with new generated passwords. The old names are the Secrets `-cnpg-authenticator` and `-duckdb`, and the login `postgrest`. `configure-db` creates the `serving` login and sets its password, `provision` records `pgrst.db_schemas` on it, and the PgDog users Secret carries it. The old login, the old Secrets and the old DuckDB secret files `ducklake_reader_pg` and `ducklake_reader` stay until someone deletes them.

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

## Security and predicate pushdown

The PostgreSQL functions are `SECURITY DEFINER` and read `access_policy` using the request claim settings. They build the allowed-row predicate and pass it into the DuckDB SQL query. This enforces RLS before the Parquet scan returns rows.

The table function builds the access policy predicate once and passes it to each source helper. See [Security](security/index.md).

The DuckLake helper also receives the columns and the `ORDER BY` with the `LIMIT` that the proxy pushed. It writes a null for each column that the request skips, so DuckDB doesn't read it. See [Query pushdown](proxy/pushdown.md). A sync run replaces the helper and the table function of each table with the new definitions.

## S3 access

The chart creates an S3 secret from `S3_ACCESS_KEY` and `S3_SECRET_KEY`. pg_duckdb uses the configured DuckDB S3 secret to read DuckLake Parquet.

The chart projects production GCP credentials into every CNPG instance through `gcp.existingSecret`, because BigQuery fallback queries can run on read replicas.

---

<!-- nav:start -->
---

[Previous: Table settings](proxy/reload.md) | [Next: Data model](model.md) | [Home](index.md)
<!-- nav:end -->
