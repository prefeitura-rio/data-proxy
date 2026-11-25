# Using the API

Data Proxy exposes synced tables through [PostgREST](https://docs.postgrest.org/). Use a JSON Web Token (JWT) and name the project and the schema in the path: `/<project>/<schema>/<table>`.

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  "${BASE_URL}/my_project/my_schema/participants?select=region_id,name&limit=20"
```

## Request routing

The proxy reads the first two segments of the path as the project and the schema. It answers 404 when the configuration has no such schema. The segments `access`, `console`, `rbac`, `dbos`, `public`, `duckdb`, `rpc`, `docs` and `v1` never name a project. The proxy replaces any `Accept-Profile` header that the client sends.

The proxy sends every request to the `postgrest` Deployment, which connects through PgDog. PgDog sends a `GET` to a standby and every write to the primary. See [Topology](operations/topology.md).

Sync tells PostgREST to reload its schema once when generated serving PostgreSQL objects change. The database waits until every standby replayed the change, then sends a `NOTIFY` on the `pgrst` channel. No pod restarts. The workflow requires its sync metric checkpoints. A failure to record a metric fails the related workflow, so operators never treat an unobserved run as successful.

## OpenAPI

When `swaggerUi.enabled` is true, the OpenAPI page is available at:

```text
${BASE_URL}/docs
```

The page can list table and column names without exposing data. Use its Authorize control with the token value only. The page adds `Bearer `.

The schema selector lists the schemas of the console as `<project>__<schema>`. The page calls the path `/<project>/<schema>/` of the chosen schema. A sync worker updates the list after each schema change and at the start of every sync run. Reload the page to see the change, which takes about a minute.

## Querying

| Need           | Example                                     |
| -------------- | ------------------------------------------- |
| Columns        | `?select=id,name`                           |
| Equality       | `?id=eq.1`                                  |
| Range          | `?updated_at=gte.2025-01-01`                |
| List           | `?id=in.(1,2,3)`                            |
| Order and page | `?order=updated_at.desc&limit=20&offset=40` |

Combine filters with `&`. PostgREST applies them with AND. row-level security (RLS) filters rows in the DuckDB scan. The proxy also pushes the filters, the selected columns and the `limit` into the scan, so they reduce the data that DuckDB reads. See [Query pushdown](proxy/pushdown.md).

A `select` with `sum()`, `avg()`, `count()`, `min()` or `max()` and a group runs inside DuckDB, so it stays fast on large tables. See [Query pushdown](proxy/pushdown.md#aggregates).

## Distinct count estimate

Each schema has a read-only RPC named `hll`. It estimates the number of distinct values of a column with a HyperLogLog sketch, in DuckDB. It takes the table in `table` and the column in `column`. The optional `group_by` gives one estimate for each value of another column, and `lg_k` sets the precision from 4 to 21. The default is 12, which gives a mean error of about 1 percent.

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --get "${BASE_URL}/my_project/my_schema/rpc/hll" \
  --data-urlencode "table=participants" \
  --data-urlencode "column=cpf" \
  --data-urlencode "group_by=city"
```

The answer is `[{"city": "Rio", "estimate": 18211}]`, or `[{"estimate": 18211}]` without `group_by`. The column and the `group_by` column must be a text, a whole number, a date or a true-or-false column. The estimate applies the row policy of the table. The function answers `409` for a table with a fallback source, because it reads only DuckLake.

Use `Prefer: count=exact` to request a total. Read the total from `Content-Range`. A count reads every row, so the proxy doesn't push the `limit` for it.

## DuckLake change feed

Each schema has a read-only RPC named `changes`. It takes the table in the argument `table`, and it accepts `start_snapshot` and an optional `end_snapshot`. It returns the DuckLake change type, the snapshot ID, the row ID and the row. The row is the object `data`, with one key for each column of the table.

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --get "${BASE_URL}/my_project/my_schema/rpc/changes" \
  --data-urlencode "table=participants" \
  --data-urlencode "start_snapshot=12" \
  --data-urlencode "end_snapshot=15" \
  --data-urlencode "select=change_type,cpf:data->>cpf"
```

A client reads a column with a JSON path, for example `data->>cpf`, and filters with `data->>cpf=eq.1`. The function replaces `<table>_dl_chg`, which gave one typed column for each column. A client of that function reads `data->>column` now.

Use the schema RPC `snapshot` to get the current cursor before or after reading a range:

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --request POST \
  "${BASE_URL}/my_project/my_schema/rpc/snapshot"
```

Snapshot bounds are inclusive. The feed returns deleted rows as preimages. The same schema scope and row policy apply to change rows, including deleted preimages. DuckLake keeps the snapshot history for seven days, so consumers must advance their cursor within that window. Compaction preserves change history, but snapshot expiration removes history that references expired snapshots.

## Renamed functions

A release renamed three functions of each data schema and one function of the `rbac` schema. The old names no longer exist, and a request to an old name answers 404. Use the new names.

| Old name | New name |
| --- | --- |
| `/rpc/dl_latest_snapshot` | `/rpc/snapshot` |
| `/rpc/dl_table_files` | `/rpc/files` |
| `/rpc/dl_chg` | `/rpc/changes` |
| `rbac.all_rows(schema)` | `rbac.whole(schema)` |

The internal table `data_proxy.table_meta` is now `data_proxy.meta`, and its column `dl_view` is now `view`. Only the sync and the generic functions read it.

Deploy the migration and the sync first, and the proxy after. Until the sync runs, `changes` and `files` refuse every table.

## Response source and cache

Read responses include:

| Header                | Values                                           |
| --------------------- | ------------------------------------------------ |
| `X-Source`            | `ducklake`, `<adapter>`, `ducklake+<adapter>`    |
| `X-DuckLake-Snapshot` | The DuckLake snapshot the response was read from |
| `X-Cache`             | `HIT`, `MISS`                                    |

`X-Source` lists the sources the request queried, not the sources that returned rows. `<adapter>` is the name of the source adapter, such as `bigquery`. A response that access control stopped has neither `X-Source` nor `X-DuckLake-Snapshot`. `X-DuckLake-Snapshot` is absent when only the source was queried. Rows from the source are always current, so they carry no snapshot.

A cache hit returns the headers of the answer it stored. Use `X-Cache` to tell a hit from a miss.

The proxy compresses a JSON answer with gzip when the request has `Accept-Encoding: gzip`. Browsers and the usual HTTP libraries send this header and decode the answer without a setting. A client that doesn't send it, such as `curl` without `--compressed`, gets the plain body. A partial answer with the status `206` isn't compressed.

### Pin a snapshot

Send `X-DuckLake-Snapshot` to read DuckLake as of one snapshot. A pinned request reads DuckLake only and never the source:

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "X-DuckLake-Snapshot: 42" \
  "${BASE_URL}/my_project/my_schema/my_table?limit=20"
```

An unknown or expired snapshot returns `404`, and a value that's not a whole number returns `400`. DuckLake keeps the snapshot history for seven days. A table that has no published data and no fallback also returns `404`.

Use these headers for troubleshooting, not authorization.

See [Security](security/index.md) for token and access-policy setup.

---

## Try it locally

Add this entry to `/etc/hosts`, forward the gateway port and ask Keycloak for a token:

```text
127.0.0.1 data-proxy.local
```

```bash
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80
token
```

`token` prints a table with the token, its type, lifetime, scope, client and realm. In Nushell, `token | get token` gives only the token. Without options, it asks the Keycloak of the local cluster for a `user` token. Use `--issuer` to ask another realm:

```bash
token --issuer https://sso.example.com/realms/prod --client-id my-client --client-secret "$SECRET"
token --issuer https://sso.example.com/realms/prod --client-id my-client --username alice --password "$PASSWORD"
```

The client secret can also come from `KEYCLOAK_CLIENT_SECRET`. `--username` and `--password` switch to the password grant, and `--scope` adds scopes.
