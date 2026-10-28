# Using the API

Data Proxy exposes synced tables through [PostgREST](https://docs.postgrest.org/). Use a JSON Web Token (JWT) and select the target schema with `Accept-Profile`.

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/participants?select=region_id,name&limit=20"
```

## Request routing

The proxy sends every request to the `postgrest` Deployment, which connects through PgDog. PgDog sends a `GET` to a standby and every write to the primary. See [Topology](operations/topology.md).

Sync tells PostgREST to reload its schema once when generated serving PostgreSQL objects change. The database waits until every standby replayed the change, then sends a `NOTIFY` on the `pgrst` channel. No pod restarts. The workflow requires its sync metric checkpoints. A failure to record a metric fails the related workflow, so operators never treat an unobserved run as successful.

## OpenAPI

When `swaggerUi.enabled` is true, the OpenAPI page is available at:

```text
${BASE_URL}/docs
```

The page can list table and column names without exposing data. Use its Authorize control with the token value only. The page adds `Bearer `.

## Querying

| Need           | Example                                     |
| -------------- | ------------------------------------------- |
| Columns        | `?select=id,name`                           |
| Equality       | `?id=eq.1`                                  |
| Range          | `?updated_at=gte.2025-01-01`                |
| List           | `?id=in.(1,2,3)`                            |
| Order and page | `?order=updated_at.desc&limit=20&offset=40` |

Combine filters with `&`. PostgREST applies them with AND. row-level security (RLS) filters rows in the DuckDB scan. The proxy also pushes the filters, the selected columns, and the `limit` into the scan, so they reduce the data that DuckDB reads. See [Query pushdown](proxy/pushdown.md).

A `select` with `sum()`, `avg()`, `count()`, `min()` or `max()` and a group runs inside DuckDB, so it stays fast on large tables. See [Query pushdown](proxy/pushdown.md#aggregates).

Use `Prefer: count=exact` to request a total. Read the total from `Content-Range`. A count reads every row, so the proxy doesn't push the `limit` for it.

## DuckLake change feed

Each configured table exposes a read-only RPC named `dl_changes_<table>` in its target schema. PostgreSQL cuts a name at 63 bytes, so the RPC of a table with a very long name has the cut name. It accepts `start_snapshot` and an optional `end_snapshot` and returns the DuckLake change type, snapshot ID, row ID, and typed row columns.

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  --get "${BASE_URL}/rpc/dl_changes_participants" \
  --data-urlencode "start_snapshot=12" \
  --data-urlencode "end_snapshot=15"
```

Use the schema RPC `dl_latest_snapshot` to get the current cursor before or after reading a range:

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  --request POST \
  "${BASE_URL}/rpc/dl_latest_snapshot"
```

Snapshot bounds are inclusive. The feed returns deleted rows as preimages. The same schema scope and row policy apply to change rows, including deleted preimages. DuckLake keeps the snapshot history for seven days, so consumers must advance their cursor within that window. Compaction preserves change history, but snapshot expiration removes history that references expired snapshots.

## Response source and cache

Read responses include:

| Header                | Values                                                    |
| --------------------- | --------------------------------------------------------- |
| `X-Source`            | `ducklake`, `bigquery`, `ducklake+bigquery`               |
| `X-DuckLake-Snapshot` | The DuckLake snapshot the response was read from          |
| `X-Cache`             | `HIT`, `MISS`                                             |

`X-Source` lists the sources the request queried, not the sources that returned rows. The value names the source adapter, and `bigquery` is the only adapter today. A response that access control stopped has neither `X-Source` nor `X-DuckLake-Snapshot`. `X-DuckLake-Snapshot` is absent when only the source was queried. Rows from the source are always current, so they carry no snapshot.

A cache hit returns the headers of the answer it stored. Use `X-Cache` to tell a hit from a miss.

### Pin a snapshot

Send `X-DuckLake-Snapshot` to read DuckLake as of one snapshot. A pinned request reads DuckLake only and never the source:

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  --header "X-DuckLake-Snapshot: 42" \
  "${BASE_URL}/my_table?limit=20"
```

An unknown or expired snapshot returns `404`, and a value that's not a whole number returns `400`. DuckLake keeps the snapshot history for seven days. A table that has no published data and no fallback also returns `404`.

Use these headers for troubleshooting, not authorization.

See [Security](security/index.md) for token and access-policy setup.

---

<!-- nav:start -->
---

[Previous: Sync](sync.md) | [Next: Proxy](proxy/index.md) | [Home](index.md)
<!-- nav:end -->
