# Retries and limits

## Retries

PostgREST fails for a few seconds when PgDog loses a standby. The proxy sends a `GET` or `HEAD` again, up to two times, when PostgREST answers `502`, `503`, or `504`, or doesn't answer. The first retry waits 500 ms and the second waits 1 second.

The client gets the last answer. A write is never sent again, because a second write could change the data twice.

## Load limit

The proxy counts the requests that wait for PostgREST, in one number for all the nginx workers of a pod. When `PROXY_MAX_INFLIGHT` requests already wait, a new request receives `503` with `{"error":"Proxy overloaded"}` and `Retry-After: 1` immediately.

A cached answer never waits for PostgREST, so a full database doesn't slow it down.

Measure the limit against the database, not the proxy. One PostgreSQL pod with 8 CPU cores answered about 120 selective lookups a second, and more waiting requests only made each of them slower. Set the limit near the number of requests that the database can run immediately (16 to 24). Multiply it by a small factor for the time that a request spends outside the database. Each proxy pod counts its own requests, so the total is the limit times the number of proxy pods.

The proxy waits at most 30 seconds for PostgREST. PostgREST waits at most `postgrest.dbPoolAcquisitionTimeout` seconds (3 by default) for a free database connection before it answers with an error.

<!-- nav:start -->
---

[Previous: Cache](cache.md) | [Next: Table settings](reload.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
