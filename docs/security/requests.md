# Requests

This page shows how the system authorizes a read request, and what each result of a request means.

## Read requests

Use a normal end-user token:

```bash
curl \
  --header "Authorization: Bearer ${USER_TOKEN}" \
  "${BASE_URL}/default/test/participants?limit=20"
```

The proxy sends every request to PostgREST, and PgDog sends a `GET` to a standby. Rows from the source follow the same authentication and row-level security (RLS) rules as DuckLake rows. [Request sequence](../architecture/serving.md#request-sequence) shows the whole read.

See [Proxy](../proxy/index.md) for the routing and cache rules. The proxy never serves the schemas `console` and `access`.

## Expected results

The table maps each result of a request to its meaning.

| Result          | Meaning                                                                   |
| --------------- | ------------------------------------------------------------------------- |
| `200` with rows | Token, schema, role, grants, and RLS checks passed.                       |
| `200 []`        | The request is valid, but the schema claim or row policy exposes no rows. |
| `401`           | The JSON Web Token (JWT) isn't valid, is expired, or can't be used by PostgREST.    |
| `403` with `RBAC: access denied` | The request has no token. Istio refuses it before the proxy. |
| `403`           | PostgreSQL/PostgREST permission or role configuration isn't valid.        |
| `404`           | The schema profile, the table, or the snapshot is unknown or unpublished. |

With `ingress.auth.enabled`, the Istio policy of the proxy needs a valid token for each request. It allows only `GET` and `HEAD` on `/health` without a token, and on `/` when the Swagger UI is on. Any other request without the `Authorization` header gets `403` with the body `RBAC: access denied`, and not `401`. It never reaches the proxy.
