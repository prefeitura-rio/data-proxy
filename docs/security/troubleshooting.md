# Troubleshooting

Use this page when a request fails or returns no rows. [Request outcomes](permissions.md#request-outcomes) lists what each status means, and the table lists what to check.

| Symptom                                       | Check                                                                                                   |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `401 Unauthorized`                            | Check the JSON Web Token (JWT) signature, issuer, audience, expiry, and `role` claim.                                    |
| `403` with the body `RBAC: access denied`     | Istio refused a request without a token. Send the header `Authorization: Bearer <token>`. Only `GET` and `HEAD` on `/health`, and on `/` with the Swagger UI, need no token. |
| `403` with `permission denied to set role`    | Check that `configure-db` completed: the `auth.servingRole` login must be a member of `anon` and of every `dp_*` role. |
| `403` with `permission denied for schema data_proxy` | Check `GRANT USAGE ON SCHEMA data_proxy TO anon/user`.                                             |
| `200 []` for every table                      | Check `schemas`, the project and the schema in the path, the configured subject claim, and the rows of `access.policy`.    |
| `404` with `the schema is not known`          | Check that the path is `/project/schema/table`, and check that the provision job ran for the schema.                              |
| Access policy write fails                     | Check that the token has `edit_policy` and `read_policy`, that its claims reach the scope of the grant, and that the request uses the profile `access`. |
| Results differ between requests               | Check `X-Source`, `X-DuckLake-Snapshot`, and `X-Cache`. Every source uses the same JWT and row-level security (RLS) rules. |

See [Using the API](../using.md), [Database Schema](../database.md) and [Proxy](../proxy/index.md) for related details.

---

<!-- nav:start -->
---

[Previous: Tokens](./tokens.md) | [Next: Console](../console/index.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
