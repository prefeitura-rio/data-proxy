# Using the API

Data Proxy exposes every synced table through [PostgREST](https://docs.postgrest.org/), a REST API generated directly from the PostgreSQL schema. This page covers the query mechanics; see [Security](security.md) for how to get a token and how row-level security decides which rows come back.

## Selecting a Schema

Each PostgreSQL schema PostgREST exposes corresponds to one key in the sync configuration's top-level `schemas` map (see [Sync](sync.md)) -- the Helm chart derives the exposed schema list automatically, there is nothing to configure separately. Select which schema a request targets with the `Accept-Profile` header:

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

Combine filters with `&`; PostgREST ANDs them together:

```bash
curl --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/participants?id_cras=eq.1&status=neq.inactive"
```

A filter on a column you are not authorized to see any rows for simply returns an empty array — row-level security filters transparently, before your query parameters are even applied. See [Security](security.md#row-level-security-rls).

## Ordering and Pagination

```bash
curl --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/participants?order=updated_at.desc&limit=20&offset=40"
```

`limit`/`offset` page through results. `postgrest.maxRows` (Helm values) caps the number of rows returned per request regardless of `limit` — requests without an explicit `limit` are capped at that value too.

## Counting Rows

Request an exact or estimated total alongside the page of results with the `Prefer` header:

```bash
curl --include --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  --header "Prefer: count=exact" \
  "${BASE_URL}/participants?limit=20"
```

The total appears in the response's `Content-Range` header, not in the JSON body.

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

A real request against a `school_district.students` table, for a user granted access to one school (`unit_type: school, unit_id: 10`) out of two in the data, returns only that school's rows:

```json
[
  { "id": 1, "full_name": "Alice Souza", "grade": 5 },
  { "id": 2, "full_name": "Bruno Lima", "grade": 4 }
]
```

A third student at a different school exists in the table but never appears in the response — RLS filtered it out before `select`/`limit` were even applied. Without a token, the same request returns `401 Unauthorized`: the `anon` role has no `GRANT` on the table at all, so there is nothing to filter.

For the full query syntax, including logical operators, embedding, and bulk writes, see the [PostgREST documentation](https://docs.postgrest.org/en/stable/references/api.html).
