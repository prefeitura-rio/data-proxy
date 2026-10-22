# Troubleshooting

[Request outcomes](permissions.md#request-outcomes) lists what each status means. The table below lists what to check.

| Symptom                                       | Check                                                                                                   |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `401 Unauthorized`                            | Check the JSON Web Token (JWT) signature, issuer, audience, expiry, and `role` claim.                                    |
| `403` with `permission denied to set role`    | Check that `configure-db` completed: the `auth.servingRole` login must be a member of `anon` and of every `dp_*` role. |
| `403` with `permission denied for schema data_proxy` | Check `GRANT USAGE ON SCHEMA data_proxy TO anon/user`.                                             |
| `200 []` for every table                      | Check `schemas`, the schema profile, the configured subject claim, and enabled `access_policy` rows.    |
| `404 Unknown schema profile`                  | Check `Accept-Profile`, and check that the provision job ran for the schema.                                                       |
| Access policy write fails                     | Check that the token has `edit_policy` and `read_policy`, that its `schemas` claim names the schema, and that `Accept-Profile` matches. |
| Results differ between requests               | Check `X-Source`, `X-DuckLake-Snapshot`, and `X-Cache`. Every source uses the same JWT and row-level security (RLS) rules. |
| `/access_policy` appears cached               | It must never be cached. Check nginx configuration and response headers.                                |

See [Using the API](../using.md), [Database Schema](../database.md), and [Proxy](../proxy.md) for related details.

---

<!-- nav:start -->
---

[Previous: Tokens](tokens.md) | [Next: Console](../console/index.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
