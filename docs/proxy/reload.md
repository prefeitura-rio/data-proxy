# Table settings

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

<!-- nav:start -->
---

[Previous: Retries and limits](limits.md) | [Next: Database Schema](../database.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
