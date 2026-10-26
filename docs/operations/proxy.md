# Proxy variables

These variables set the roles of the serving layer, the link from the sync workers to the proxy, and the settings of the OpenResty proxy. [Environment Variables](environment.md) has the variables of the other components.

## Auth and proxy

These variables set the PostgreSQL roles of the serving layer and the settings that connect the sync workers to the proxy and the provision job.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUTH_ANON_ROLE` | `anon` | Unauthenticated PostgreSQL role. |
| `AUTH_USER_ROLE` | `dp_api` | The baseline PostgreSQL role. Every role of `rbac.toml` is a member of it. The chart sets it with `auth.userRole`. |
| `AUTH_SERVING_ROLE` | `serving` | The login of the serving layer: PostgREST, PgDog and the recovery Deployment. |
| `PROXY_CACHE_REDIS_DB` | `1` | Redis database for cached proxy responses. |
| `PROXY_ADMIN_URL` | unset | Address of the reload endpoint of the proxy, for example `http://data-proxy-proxy-admin:8081/reload`. The sync workers resolve its host to every proxy pod and send each one a reload request. Unset: nobody is notified. The chart sets it. |
| `PROVISION_CRONJOB` | unset | Name of the suspended `provision` CronJob. A sync worker makes a Job from it when a schema changes and at the start of every sync run. The chart sets it. Unset, the worker skips the step. |
| `PROVISION_TIMEOUT_SECONDS` | `600` | How long the worker waits for the provision Job. |

## OpenResty proxy

The Helm chart sets these variables on the proxy Deployment. The Lua code reads them through `config.load` in `proxy/src/proxy/config.lua`. The proxy loads all settings once at start-up.

It collects the text of the variables in one table. Then it converts and checks that table once with the `config` schema (`proxy/src/proxy/schemas/definitions/config.lua`). A variable that isn't set uses the default of its setting.

A missing required variable, a value of the wrong type, or a value outside the limits stops nginx from starting. The error names the setting. A variable name is `PROXY_` and the upper-case name of the setting. `helm/files/config/nginx.conf` lists every variable with an `env` directive, because nginx removes the other variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROXY_CACHE_TTL` | `300` | Default cache lifetime in seconds, `0` or more. A table in `sync.json` can set its own `cache_ttl`. |
| `PROXY_MAX_BODY` | `262144` | Largest response body in bytes that the proxy stores. `0` means no limit. |
| `PROXY_REDIS_HOST` | required | Redis host for the cache. The operator provides one stable endpoint. Give nginx a name with a dot, such as `valkey.<namespace>.svc.cluster.local`, because its resolver doesn't use the search domains. The chart adds the namespace to a name with no dot. |
| `PROXY_REDIS_PORT` | `6379` | Redis port for the cache, from `1` to `65535`. |
| `PROXY_REDIS_DATABASE` | `1` | Redis database for cached responses, `0` or more. |
| `PROXY_REDIS_PASSWORD` | unset | Redis password. Omit it for no login. |
| `PROXY_POSTGREST` | required | Base address of the PostgREST service. |
| `PROXY_MAX_INFLIGHT` | `0` | Most requests that wait for PostgREST at the same time in one proxy pod. More get a `503` with `Retry-After`. `0` means no limit. The chart sets `32`. |
| `PROXY_CONFIG_URL` | unset | Address of the configuration function of PostgREST, for example `http://postgrest:3000/rpc/config`. The proxy sends the profile `console` with it. When it's set, the proxy takes its table settings from there. The chart sets it. |
| `PROXY_CONFIG_REFRESH_SECONDS` | `60` | Seconds between two checks of PostgREST for a new document. The check is a safety net: the sync workers also call the reload endpoint immediately after a change. |

<!-- nav:start -->
---

[Previous: Environment Variables](environment.md) | [Next: KEDA Scaling](keda.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
