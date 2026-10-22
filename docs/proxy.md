# Proxy

The proxy stands before PostgREST in the request path. It forwards requests and caches read responses. It doesn't choose where data comes from: PostgreSQL decides that for every request.

The chart deploys the proxy as OpenResty and connects directly to Redis. Configure it under `proxy` in [Helm Chart](operations/helm.md#proxy).

## Request flow

The proxy checks Redis first, calls PostgREST on a miss, and stores the answer. [Request sequence](architecture/serving.md#request-sequence) shows the whole read, step by step.

## Choosing the source

The table view calls one function that applies these rules in order:

1. PostgreSQL reads a request that pins a snapshot from DuckLake only.
2. A partitioned table that's not in `data_proxy.state` reads its configured source when `fallback` is true. Without fallback, the request receives a `404`.
3. A table in `data_proxy.state` reads DuckLake. A partitioned table with `fallback: true` also reads uncovered partitions from its configured source.

Row-level security runs first. A request that RLS stops reads no source and gets an empty list without `X-Source`.

An empty DuckLake result never triggers fallback. With `fallback: true`, every request also reads the configured source for partitions that DuckLake doesn't store, because PostgREST applies filters after the function returns. Keep fallback disabled when that cost isn't acceptable.

## Filter pushdown

The proxy forwards only the `where` part of a query to PostgreSQL, in the `X-DuckLake-Filter` header. The table view can then filter rows before it reads the sources.

Everything else stays with PostgREST: `order`, `limit`, `offset`, `select`, embeds, and content negotiation.

The parser reads the query parameters that contain comparisons and the `and`, `or` and `not` groups. It decodes the escapes in their keys and values. The parser skips a parameter that its grammar doesn't accept, and PostgREST then applies the whole query. The grammar is LPeg, in `proxy/src/proxy/parser/patterns.lua`. The proxy checks the document that the parser builds with the `filter_document` schema before it sends it. A filter that the PostgreSQL side can't render therefore fails in the proxy, with the field named.

## Cache

- The proxy stores only non-empty `GET` answers with status 200. An empty answer can mean three things: access was denied, the data isn't published yet, or the access policy changed. The proxy never stores it.
- An entry stores the body with `X-Source` and `X-DuckLake-Snapshot`, so a cache hit reports what produced the answer. `X-Cache` tells a hit from a miss.
- OpenResty doesn't cache ranged, write, oversized, or non-JSON responses. `/access_policy` and its subpaths are never cached.
- Cache reads use the Redis reader endpoint. Cache writes use the Redis writer endpoint.
- The sync service flushes the cache after each sync run.
- The key has the parts in the table below, and the table also has the two counters that keep the cache current.
- When an entry is missing, only one request for that key asks PostgREST. The lock section shows how.

### The cache key

| Key part | Why it's in the key |
| --- | --- |
| Method, path and query | They name the request. |
| Schema profile and representation headers | The same path can answer in another schema or format. |
| The pinned snapshot (`X-DuckLake-Snapshot`) | A pinned read must not share an entry with the latest data. |
| The user, the schemas and the role | A token with another identity never reads an answer that another token cached. |
| The access policy generation | A write to `/access_policy` through the proxy raises this counter in Redis. |
| The settings revision | `console.config()` contains it in its document. Any other change of the access policy or the rules moves `rbac.revision`. |

A trigger on `rbac.revision` queues a `reload_proxy` workflow. The sync worker asks every proxy pod to read the settings. A proxy that takes a new document stops serving the old answers immediately. Examples of such a change are a write to the access policy from the console, and a new `rbac.toml` that `configure-db` loads.

The revision comes from the document, not from Redis. A flush of the cache, a restart of the proxy, and a new proxy pod all leave it right. The proxy finds a change that happened while no proxy ran when it reads the document.

### The lock

Without a lock, a slow entry that expires sends every request for it to PostgREST at the same time. A table that reads the source is an example. The requests fill the connections of PostgREST and the limit of `proxy.maxInflight`, and the proxy then answers 503 for every route.

The proxy calls the first request for a key the leader. The other requests for the same key are followers, and they wait for the leader.

![Cache lock sequence](diagrams/architecture/cache-sequence.svg)

- A follower waits up to 30 seconds and reads the entry that the leader stored.
- The lock is `resty.lock`, which OpenResty ships, in the shared dictionary `proxy_locks`. A leader that dies frees the key after 60 seconds.
- The wait is per proxy pod. A request that waits doesn't count against `proxy.maxInflight`.
- A follower asks PostgREST itself if the leader stored nothing (for example after an error) or if the wait times out.

See [Using the API](using.md#response-source-and-cache) for the response headers.

## Retries

PostgREST fails for a few seconds when PgDog loses a standby. The proxy sends a `GET` or `HEAD` again, up to two times, when PostgREST answers `502`, `503`, or `504`, or doesn't answer. The first retry waits 500 ms and the second waits 1 second.

The client gets the last answer. A write is never sent again, because a second write could change the data twice.

## Load limit

The proxy counts the requests that wait for PostgREST, in one number for all the nginx workers of a pod. When `PROXY_MAX_INFLIGHT` requests already wait, a new request receives `503` with `{"error":"Proxy overloaded"}` and `Retry-After: 1` immediately.

A cached answer never waits for PostgREST, so a full database doesn't slow it down.

Measure the limit against the database, not the proxy. One PostgreSQL pod with 8 CPU cores answered about 120 selective lookups a second, and more waiting requests only made each of them slower. Set the limit near the number of requests that the database can run immediately (16 to 24). Multiply it by a small factor for the time that a request spends outside the database. Each proxy pod counts its own requests, so the total is the limit times the number of proxy pods.

The proxy waits at most 30 seconds for PostgREST. PostgREST waits at most `postgrest.dbPoolAcquisitionTimeout` seconds (3 by default) for a free database connection before it answers with an error.

## Table settings from the configuration

The proxy needs two parts of `sync.json`: the cache lifetime of each table (`cache_ttl`) and the paths that it never caches (`noCachePaths`).

With `PROXY_CONFIG_URL` set, it takes both from PostgREST: `GET /rpc/config` with the header `Accept-Profile: console`. The function `console.config()` builds the document from the configuration tables of the console. It contains only the table names and their `cache_ttl`. All nginx workers of a pod share one copy, and a worker parses it again only when the copy changes.

The proxy needs no credentials for this call. The chart grants the anonymous role the `EXECUTE` right on that one function, and nothing else in the `console` schema. To keep the endpoint inside the cluster, the proxy refuses every request that has the profile `console` in `Accept-Profile` or `Content-Profile`. It answers 403. The proxy calls PostgREST for the configuration on its own, so this block doesn't touch the call.

The first `sync.json` comes from the file `/config/sync.json` when the file exists. The chart doesn't mount one. Before the first answer of PostgREST, the proxy uses its defaults: the lifetime of `PROXY_CACHE_TTL` and no cache for `/access_policy`.

The proxy takes a new document in three ways:

| Way | When |
| --- | --- |
| At start | Worker 0 asks PostgREST as soon as nginx starts. |
| `POST /reload` on the admin port 8081 | The sync workers call it after every change of a configuration table. The answer is `{"changed":true}`, or `changed` is `false` when the proxy already has that document. |
| A check every `PROXY_CONFIG_REFRESH_SECONDS` | Worker 0 asks again, so a proxy that missed a call still catches up. |

The Istio policy of the proxy allows `POST /reload` on port 8081 and nothing else there. The sync workers have no sidecar, so the rule can't name them. The gateway never routes to that port, so only the cluster reaches it.

A trigger on the three configuration tables queues a `reload_proxy` workflow. A burst of edits shares one waiting reload, and a change during a reload queues the next one. The worker resolves the headless Service `<release>-proxy-admin` to the address of every proxy pod and sends the request to each one. If a pod fails, DBOS repeats the step.

A document that doesn't match the schema, or an unreachable PostgREST, changes nothing. The proxy keeps the last good settings, and `POST /reload` answers 502 with the reason. `POST /reload` answers 409 when `PROXY_CONFIG_URL` isn't set, and 405 for any other method.

The admin port has no authentication, so no ingress may route it.

## Settings

The Helm Deployment sets the proxy settings as `PROXY_*` environment variables. See [Environment Variables](operations/environment.md#openresty-proxy) for each variable, its default, and its meaning.

`helm/files/config/nginx.conf` lists every variable with an `env` directive, because nginx removes the other variables.

Lua code reads settings only through the table that `config.load` returns:

- `main` loads the table once at start-up, in `init_by_lua`, and passes it down.
- The load collects the text of every variable in one table. It converts and checks that table once with the `config` schema, which also sets the defaults.
- A missing required variable, a value of the wrong type, or a value outside the limits stops nginx from starting. The error names the setting.
- The variable is `PROXY_` and the upper-case name of the setting.

`sync.json` sets only the cache time of each table and the paths that are never cached. The proxy checks it with a schema when it starts. The proxy checks other data that it doesn't create in the same way. This data is the entries that it reads from Redis and the claims of a bearer token.

The schema code works like this:

- The `Schema` class is in `proxy/src/proxy/schemas/schema.lua`.
- The registry is `proxy/src/proxy/schemas/main.lua` (`schemas:register(name, fields)`). When the registry module loads at start-up, it registers every file `proxy/src/proxy/schemas/definitions/<name>.lua` as the schema `<name>`.
- A definition file declares only the fields.
- Code checks data with `schemas.<name>:validate(value)`.

---

<!-- nav:start -->
---

[Previous: Using the API](using.md) | [Next: Database Schema](database.md) | [Home](index.md)
<!-- nav:end -->
