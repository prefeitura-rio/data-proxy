# Fallback

Fallback serves an empty local `GET` from the BigQuery-backed `<table>_bq` view. Clients keep using the normal table endpoint.

Enable it with:

```yaml
fallback:
  enabled: true
```

The chart deploys nginx and Valkey. nginx is the public endpoint. GET and HEAD requests use PostgREST-ro through the read Pooler; mutations use PostgREST-rw directly through the current writer. PostgREST remains the local API endpoint.

## Request flow

```mermaid
sequenceDiagram
    participant C as Client
    participant N as nginx
    participant V as Valkey
    participant P as PostgREST
    participant B as BigQuery (_bq view)

    C->>N: GET /table (JWT)
    N->>V: cache lookup (key = method+path+query+identity)
    alt cache hit
        V-->>N: cached rows
        N-->>C: 200 (from cache)
    else cache miss
        V-->>N: miss
        N->>P: GET /table (JWT forwarded)
        P-->>N: local rows
        alt local rows present
            N-->>C: 200 (from local)
        else local table empty
            N->>P: GET /table_bq (JWT forwarded)
            P->>B: SELECT ... FROM table_bq
            B-->>P: fallback rows
            P-->>N: fallback rows
            alt fallback rows present
                N->>V: cache store (key, rows)
                N-->>C: 200 (from fallback)
            else fallback empty or error
                N-->>C: 200 (empty local response)
            end
        end
    end
```

Fallback applies to reads only. The proxy does not cache empty, ranged, write, oversized, or non-JSON responses. `/access_policy` and its subpaths are never read from or written to the response cache, regardless of schema profile.

## Access and cache scope

nginx forwards the JWT to PostgREST. Local tables and fallback views use the same schema and row conditions.

Cache keys include request method, path, query, schema profile, representation headers, and identity claims. Different identities do not share entries.

Configure fallback values in [Helm Chart](helm_chart.md#bigquery-fallback). API response headers are documented in [Using the API](using.md#response-source-and-cache).

---

[← Previous](security.md) · [Home](../README.md) · [Next →](keda.md)
