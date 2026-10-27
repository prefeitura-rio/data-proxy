# Proxy

The proxy stands before PostgREST in the request path. It forwards requests and caches read responses. It doesn't choose where data comes from: PostgreSQL decides that for every request.

The chart deploys the proxy as OpenResty and connects directly to Redis. Configure it under `proxy` in [Helm Chart](../operations/helm.md#proxy).

## Request flow

The proxy checks Redis first, calls PostgREST on a miss, and stores the answer. [Request sequence](../architecture/serving.md#request-sequence) shows the whole read, step by step.

## Choosing the source

The table view calls one function that applies these rules in order:

1. PostgreSQL reads a request that pins a snapshot from DuckLake only.
2. A partitioned table that's not in `data_proxy.state` reads its configured source when `fallback` is true. Without fallback, the request receives a `404`.
3. A table in `data_proxy.state` reads DuckLake. A partitioned table with `fallback: true` also reads uncovered partitions from its configured source.

Row-level security runs first. A request that RLS stops reads no source and gets an empty list without `X-Source`.

An empty DuckLake result never triggers fallback. With `fallback: true`, every request also reads the configured source for partitions that DuckLake doesn't store, because PostgREST applies filters after the function returns. Keep fallback disabled when that cost isn't acceptable.

## Settings

The Helm Deployment sets the proxy settings as `PROXY_*` environment variables. See [Environment Variables](../operations/proxy.md#openresty-proxy) for each variable, its default, and its meaning.

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

## Pages

- [Query pushdown](pushdown.md) — what the proxy sends to PostgreSQL besides the filter.
- [Cache](cache.md) — what the proxy stores, the cache key, and the lock.
- [Retries and limits](limits.md) — retries of a read, the load limit, and the time limits.
- [Table settings](reload.md) — how the proxy takes the table settings from the configuration.

<!-- nav:start -->
---

[Previous: Using the API](../using.md) | [Next: Query pushdown](pushdown.md) | [Home](../index.md)
<!-- nav:end -->
