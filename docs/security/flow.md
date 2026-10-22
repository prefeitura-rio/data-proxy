# Authorization flow

This page shows how a token becomes a database role and a row filter, and which PostgreSQL roles take part.

The proxy puts the role in the cache key, so a token with another role never gets an answer that another token cached.

[Request sequence](../architecture/serving.md#request-sequence) shows the JSON Web Token (JWT) and row-level security (RLS) flow. The gateway checks the token, PostgREST switches to the role of the token, and the database applies the checks.

CloudNativePG (CNPG) manages two roles: `ops` (the owner and a superuser) and `duckdb` (the read-only catalog login). The `configure-db` job creates the rest as `ops`:

| Role                     | Purpose                                                                        |
| ------------------------ | ------------------------------------------------------------------------------ |
| `anon`                   | Role of a request without a token, with no application table access.           |
| `dp_api`, `dp_policy`, `dp_console`, `dp_admin` | One role for each role of `rbac.toml`. A token switches to the role of its `role` claim, and the rules decide what that role may do. The schema and row policies limit the rows. |
| `serving`                | The login of the serving layer: PostgREST, the PgDog pool and the recovery Deployment (`auth.servingRole`). It's `NOINHERIT` and can switch to `anon` and to every `dp_*` role. |

[Database](../database.md#roles) describes each role and why `ops` is a superuser.

Init-db creates the `data_proxy` schema and its internal functions, procedures, and grants. `data_proxy` isn't in `PGRST_DB_SCHEMAS`, so these objects aren't exposed as RPC endpoints. `data_proxy.pre_request()` copies JWT claims into transaction-local PostgreSQL settings. `USAGE` on `data_proxy` doesn't grant application table access.

<!-- nav:start -->
---

[Previous: Roles](roles.md) | [Next: Schema and row authorization](authorization.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
