# Database roles and grants

This page describes the PostgreSQL roles, the security of the functions, the pushdown of the predicates and the S3 access. [Database Schema](database.md) describes the schemas.

## Roles

| Role | Purpose |
| --- | --- |
| `anon` | The role of a request without a token. It has no access to application data. |
| `dp_api`, `dp_policy`, `dp_console`, `dp_admin` | One role for each role of `rbac.toml`. A token switches to the role of its `role` claim. The last three roles are members of `dp_api` and share its grants. `rbac.can()` and `rbac.mine()` decide what each role may do. |
| `serving` | The login of the serving layer (`auth.servingRole`). PostgREST, the PgDog pool and the `recovery` Deployment connect with it. It's `NOINHERIT` and a member of `anon` and of every `dp_*` role. It has no rights of its own and only switches into the request role of the token. `configure-db` creates it with the password of its Secret. No token can switch to it. |
| `ops` | The owner of both databases (`cnpg.db.user`). It's the login of every internal workload: the sync workers, the recovery deployment, the chart jobs, the backup, and the autoscaler. It's a superuser. pg_duckdb turns the local file system off for every other role, and DuckDB loads its extensions and writes its persistent secrets there. It owns the `SECURITY DEFINER` functions that run DuckDB. No token can switch to it. |
| `duckdb` | The read-only login of the catalog database. pg_duckdb uses it to read the metadata schemas. |

`configure-db` creates `anon`, the `dp_*` roles and `serving` as `ops`, so a new role in `rbac.toml` needs no chart change.

The Secret of a login has the name `<release>-cnpg-<role>`: `-cnpg-ops`, `-cnpg-serving` and `-cnpg-duckdb`. After an upgrade of an install that used the old names, the chart creates the new Secrets with new generated passwords. The old names are the Secrets `-cnpg-authenticator` and `-duckdb`, and the login `postgrest`. `configure-db` creates the `serving` login and sets its password, `provision` records `pgrst.db_schemas` on it, and the PgDog users Secret carries it. The old login, the old Secrets and the old DuckDB secret files `ducklake_reader_pg` and `ducklake_reader` stay until someone deletes them.

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

[Previous: Database Schema](./database.md) | [Next: Data model](./model.md) | [Index](./database.md) | [Home](./index.md)
<!-- nav:end -->
