# Using the API

Data Proxy exposes every synced table through [PostgREST](https://docs.postgrest.org/). PostgREST is a REST API generated directly from the PostgreSQL schema. This page covers the query mechanics. See [Security](security.md) to learn how to get a token. See [Security](security.md) to learn how row-level security selects which rows come back.

## Browsing the API

PostgREST generates an OpenAPI spec from the exposed schemas. This spec lists every table, column, and operation. This spec is plain JSON, at the API's root URL (`${BASE_URL}/`). This JSON renders as a page, not raw text, when `swaggerUi.enabled` is `true` in the Helm chart. This page lives at `${BASE_URL}/docs`.

The page needs `ingress.enabled` set to `true`. Anyone can open the page without a token. `swaggerUi.enabled` also sets `PGRST_OPENAPI_MODE` to `ignore-privileges`. This setting makes the page list every table, for every role. No token exposes one row of real data. No missing `access_policy` row exposes one row of real data either. Only table and column names appear this way.

The page loads its spec with an unauthenticated `GET /` call. This call happens before the "Authorize" button even exists. `swaggerUi.enabled` also adds one exception to the Istio authorization rules. An unauthenticated `GET` to the exact path `/` needs no token. Every other path and every other method on PostgREST still needs one.

The page has its own "Authorize" button, near the top. Paste `${TOKEN}` there, with no `Bearer ` prefix. The page adds this prefix on its own. Every "Try it out" call on the page then carries this token. Each call returns real rows through the same RLS check as a plain `curl` request. See [Security](security.md) to get a token.

## Selecting a Schema

Each PostgreSQL schema that PostgREST exposes matches one key in the sync configuration's top-level `schemas` map (see [Sync](sync.md)). The Helm chart derives the exposed schema list on its own. You do not configure this list separately. Select the target schema for a request with the `Accept-Profile` header:

```bash
curl --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/table"
```

Omit the header only if the deployment exposes a single schema.

## Selecting Columns

Use `select` to request specific columns instead of every column:

```bash
curl --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/participants?select=id_cras,name,updated_at"
```

## Filtering Rows

Every column supports a PostgREST operator as a query parameter: `column=operator.value`.

| Operator   | Meaning                  | Example                     |
| ---------- | ------------------------ | --------------------------- |
| `eq`       | equals                   | `id_cras=eq.1`              |
| `neq`      | not equal                | `status=neq.inactive`       |
| `gt`/`gte` | greater than (or equal)  | `updated_at=gte.2025-01-01` |
| `lt`/`lte` | less than (or equal)     | `age=lt.18`                 |
| `like`     | pattern match (`%`, `_`) | `name=like.*Silva*`         |
| `in`       | one of a list            | `id_cras=in.(1,2,3)`        |
| `is`       | `null`/`true`/`false`    | `deleted_at=is.null`        |

Combine filters with `&`. PostgREST joins them with AND:

```bash
curl --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/participants?id_cras=eq.1&status=neq.inactive"
```

A filter on a column may return an empty array. This happens when you have no access to any row in that table. Row-level security filters the rows before it applies your query parameters. See [Security](security.md#row-level-security-rls).

## Ordering and Pagination

```bash
curl --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/participants?order=updated_at.desc&limit=20&offset=40"
```

`limit` and `offset` page through results. `postgrest.maxRows` (Helm values) caps the number of rows returned per request. This cap applies even when `limit` requests more rows. This cap also applies when a request sets no `limit` at all.

## Counting Rows

Request an exact or estimated total alongside the page of results with the `Prefer` header:

```bash
curl --include --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  --header "Prefer: count=exact" \
  "${BASE_URL}/participants?limit=20"
```

The total appears in the response's `Content-Range` header, not in the JSON body.

## Data Freshness

Each configured schema has a `freshness` endpoint. Use the schema profile of the data table:

```bash
curl --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/freshness?table=eq.participants"
```

A full table has one row. Its `partition` value is null. A partitioned table has one row for each known partition.

- **`updated_at`**: UTC time of the last publication.
- **`attempted_at`**: UTC time of the latest synchronization attempt.
- **`status`**: Result of the latest attempt. The value is `success` or `failure`.

After a failed update of an existing partition, `updated_at` does not change. Data Proxy continues to serve the old partition data. `attempted_at` changes. The `status` value is `failure`. A failed new partition has a null `updated_at` value. Data Proxy has not published data for this partition.

## Full Example

```bash
TOKEN="$(
  curl --fail --silent --show-error \
    --request POST \
    --header "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=${CLIENT_ID}" \
    --data-urlencode "client_secret=${CLIENT_SECRET}" \
    "${TOKEN_URL}" |
    jq --exit-status --raw-output '.access_token'
)"

curl --fail --silent --show-error \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: ${SCHEMA}" \
  "${BASE_URL}/${TABLE}?select=col1,col2&limit=10"
```

Take a real request against a `school_district.students` table. The data holds rows for two schools. The requesting user has access to only one school (`unit_type: school, unit_id: 10`). The response holds only that school's rows:

```json
[
  { "id": 1, "full_name": "Alice Souza", "grade": 5 },
  { "id": 2, "full_name": "Bruno Lima", "grade": 4 }
]
```

A third student exists in the table, at a different school. This student never appears in the response. RLS filters out this row before it applies `select` or `limit`. The same request without a token returns `401 Unauthorized`. The `anon` role has no `GRANT` on the table. There is nothing left to filter.

For the full query syntax, including logical operators, embedding, and bulk writes, see the [PostgREST documentation](https://docs.postgrest.org/en/stable/references/api.html).
