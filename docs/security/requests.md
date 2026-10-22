# Requests

This page shows how the system authorizes a read request, and what each result of a request means.

## Read requests

Use a normal end-user token:

```bash
curl \
  --header "Authorization: Bearer ${USER_TOKEN}" \
  --header "Accept-Profile: test" \
  "${BASE_URL}/participants?limit=20"
```

The proxy sends every request to PostgREST, and PgDog sends a `GET` to a standby. Rows from the source follow the same authentication and row-level security (RLS) rules as DuckLake rows. [Request sequence](../architecture/serving.md#request-sequence) shows the whole read.

See [Proxy](../proxy.md) for the routing and cache rules. `/access_policy` is never response-cached.

## Expected results

The table maps each result of a request to its meaning.

| Result          | Meaning                                                                   |
| --------------- | ------------------------------------------------------------------------- |
| `200` with rows | Token, schema, role, grants, and RLS checks passed.                       |
| `200 []`        | The request is valid, but the schema claim or row policy exposes no rows. |
| `401`           | The JSON Web Token (JWT) is missing, not valid, expired, or can't be used by PostgREST.    |
| `403`           | PostgreSQL/PostgREST permission or role configuration isn't valid.        |
| `404`           | The schema profile, the table, or the snapshot is unknown or unpublished. |

<!-- nav:start -->
---

[Previous: Schema and row authorization](authorization.md) | [Next: Identity provider](identity.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
