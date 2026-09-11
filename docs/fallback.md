# BigQuery Fallback

## Purpose

The fallback serves a read when the local PostgreSQL table has no rows. It uses a BigQuery-backed `<table>_bq` view. Clients continue to use the normal table endpoint.

The fallback does not replace the sync pipeline. BigQuery remains the source of truth. PostgreSQL remains the normal read source.

## Enable the fallback

Set this Helm value:

```yaml
fallback:
  enabled: true
```

The chart then creates an nginx proxy and a Webdis sidecar. The proxy is the public read endpoint. PostgREST remains the local API endpoint.

## Read flow

For a `GET` request, the proxy does these steps:

1. Read Webdis for a cached response.
2. If a cached response exists, return it.
3. Query the local PostgREST table.
4. If the local response has rows, return it.
5. If the local response is empty and fallback is enabled, query `<table>_bq` through PostgREST.
6. If the fallback response has rows, return it and store it in Webdis.
7. If the fallback response is empty or fails, return the local response.

The proxy never uses the fallback for write requests.

## Access control

The proxy forwards the client JWT to PostgREST. PostgREST validates the JWT and applies the same RLS policy to local tables and `_bq` views.

The cache key contains the request method, path, query, identity claims, schema profile, and representation headers. Different users do not share cache entries when their claims differ.

The proxy does not cache empty responses, ranged responses, write responses, oversized responses, or non-JSON responses.

## Response headers

The proxy returns these headers:

| Header     | Values                                   | Meaning                 |
| ---------- | ---------------------------------------- | ----------------------- |
| `X-Source` | `cache`, `postgrest`, `bigquery`, `none` | Source of the response. |
| `X-Cache`  | `HIT`, `MISS`                            | Cache result.           |

`X-Source: cache` always has `X-Cache: HIT`.

## Proxy logs

The proxy writes structured JSON request logs to the nginx error stream. Important fields are:

| Field     | Unit         | Meaning                                    |
| --------- | ------------ | ------------------------------------------ |
| `source`  | —            | Response source.                           |
| `status`  | HTTP status  | Response status.                           |
| `wait_ms` | milliseconds | Time from proxy request start to response. |
| `bytes`   | bytes        | Response body size.                        |

The proxy disables nginx access logs. Use the structured proxy logs for request analysis.

## Load-test limits

The local load profile uses these p95 limits:

| Source     | Metric                  | Limit   |
| ---------- | ----------------------- | ------- |
| Cache      | `cache_duration_ms`     | 50 ms   |
| PostgreSQL | `postgrest_duration_ms` | 300 ms  |
| BigQuery   | `bigquery_duration_ms`  | 4000 ms |

The load test keeps five local daily partitions for `protocolo_estado_diario`. It sends paired requests to older BigQuery-only partitions. The first request measures BigQuery or a warm cache entry. The second request measures a cache hit.
