# Using the API

Data Proxy exposes synced tables through [PostgREST](https://docs.postgrest.org/). Use a JWT and select the target schema with `Accept-Profile`.

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/participants?select=id_cras,name&limit=20"
```

## Read and write routing

nginx routes `GET` and `HEAD` requests to PostgREST-ro, which uses the CNPG read Pooler. POST, PUT, PATCH, and DELETE requests route to PostgREST-rw, which connects directly to the current CNPG writer. This separation keeps reads replica-friendly while writes use the primary.

The read and write deployments are refreshed by the Publisher after replica replay. A PostgREST deployment is not Ready until its HTTP readiness probe serves `/`.

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

## Response source and cache

When fallback is enabled, read responses include:

| Header     | Values                                   |
| ---------- | ---------------------------------------- |
| `X-Source` | `cache`, `postgrest`, `bigquery`, `none` |
| `X-Cache`  | `HIT`, `MISS`                            |

Use these headers for troubleshooting, not authorization.

## Freshness

Each schema exposes `freshness`:

```bash
curl \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: my_schema" \
  "${BASE_URL}/freshness?table=eq.participants"
```

`updated_at` is the last successful publication time. `attempted_at` is the latest attempt. `status` is `success` or `failure`. A failed existing partition keeps its old data and `updated_at`; a failed new partition has no `updated_at`.

See [Security](security.md) for token and access-policy setup.

---

[← Previous](sync.md) · [Home](../README.md) · [Next →](security.md)
