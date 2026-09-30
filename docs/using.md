# Using the API

Data Proxy exposes synced tables through [PostgREST](https://docs.postgrest.org/). Use a JWT and select the target schema with `Accept-Profile`.

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/participants?select=region_id,name&limit=20"
```

## Request routing

nginx routes all requests to the single PostgREST deployment, which connects through the CNPG session-mode read Pooler. The PostgREST deployment is refreshed after sync when the view set changes. It is not Ready until its HTTP readiness probe serves `/`.

## OpenAPI

When `swaggerUi.enabled` is true, the OpenAPI page is available at:

```text
${BASE_URL}/docs
```

The page can list table and column names without exposing data. Use its Authorize control with the token value only; the page adds `Bearer `.

## Querying

| Need           | Example                                     |
| -------------- | ------------------------------------------- |
| Columns        | `?select=id,name`                           |
| Equality       | `?id=eq.1`                                  |
| Range          | `?updated_at=gte.2025-01-01`                |
| List           | `?id=in.(1,2,3)`                            |
| Order and page | `?order=updated_at.desc&limit=20&offset=40` |

Combine filters with `&`. PostgREST applies them with AND. RLS filters rows before query filters.

Use `Prefer: count=exact` to request a total. Read the total from `Content-Range`.

## DuckLake change feed

Each configured table exposes a read-only RPC named `ducklake_changes_<table>` in its target schema. It accepts `start_snapshot` and an optional `end_snapshot` and returns the DuckLake change type, snapshot ID, row ID, and typed row columns.

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  --get "${BASE_URL}/rpc/ducklake_changes_participants" \
  --data-urlencode "start_snapshot=12" \
  --data-urlencode "end_snapshot=15"
```

Use the schema RPC `ducklake_latest_snapshot` to get the current cursor before or after reading a range:

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  --request POST \
  "${BASE_URL}/rpc/ducklake_latest_snapshot"
```

Snapshot bounds are inclusive. Deleted rows are returned as preimages. The same schema scope and row-level policy apply to change rows, including deleted preimages. Snapshot history is retained for seven days, so consumers must advance their cursor within that window. Compaction preserves change history, but snapshot expiration removes history that references expired snapshots.

## Response source and cache

Read responses include:

| Header                | Values                                                    |
| --------------------- | --------------------------------------------------------- |
| `X-Source`            | `ducklake`, `bigquery`, `ducklake+bigquery`               |
| `X-DuckLake-Snapshot` | The DuckLake snapshot the response was read from          |
| `X-Cache`             | `HIT`, `MISS`                                             |

`X-Source` lists the sources the request queried, not the sources that returned rows. A response that access control stopped has neither `X-Source` nor `X-DuckLake-Snapshot`. `X-DuckLake-Snapshot` is absent when only BigQuery was queried. BigQuery rows are always current; they carry no snapshot.

A cache hit returns the headers of the answer it stored. Use `X-Cache` to tell a hit from a miss.

### Pin a snapshot

Send `X-DuckLake-Snapshot` to read DuckLake as of one snapshot. A pinned request reads DuckLake only and never BigQuery:

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  --header "X-DuckLake-Snapshot: 42" \
  "${BASE_URL}/my_table?limit=20"
```

An unknown or expired snapshot returns `404`, and a value that is not a whole number returns `400`. Snapshot history is retained for seven days.

Use these headers for troubleshooting, not authorization.

See [Security](security.md) for token and access-policy setup.

---

[← Previous](sync.md) · [Home](../README.md) · [Next →](security.md)
