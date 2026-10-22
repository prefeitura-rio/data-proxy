# Helm Chart

The Helm chart deploys Data Proxy: the CloudNativePG (CNPG) cluster, PgDog, PostgREST, the proxy, the sync workers, the console and the notebooks.

## Objects

The chart creates these main objects. Each name starts with the release name.

| Object | Kind | Purpose |
| --- | --- | --- |
| The release name | CNPG `Cluster` | The PostgreSQL primary and standbys. |
| `-proxy`, `-postgrest`, `-pgdog` | Deployment | The serving layer. KEDA scales the proxy and PostgREST. |
| `-sync` | Deployment | The sync workers. KEDA scales them. |
| `-recovery` | Deployment | Recovers the DBOS workflows that a dead sync pod left behind. |
| `-console`, `-notebooks` | Deployment | The console and the notebook site. |
| `-swagger-ui` | Deployment | The Swagger UI of the API. |
| `-configure-db`, `-migrate-db`, `-provision` | Job | The install Jobs of [Initialization](initialization.md). `configure-db` and `provision` also have a suspended CronJob of the same name. |
| `-s3-bootstrap` | Job | Creates the S3 bucket or waits for it. |
| `-backup`, `-maintenance` | CronJob | The access policy backup and the database maintenance. Maintenance also deletes the DBOS workflows that finished more than `jobs.maintenance.workflowRetentionDays` days ago (90 by default). |
| `-duckdb` | PersistentVolumeClaim | The shared volume of the DuckDB secrets. |

## Prerequisites

Install [KEDA](https://keda.sh/docs/latest/deploy/). Install Istio when `ingress.enabled` is `true`. Install the CloudNativePG operator 1.26 or newer, because the cluster uses `probes.readiness.type` and `maximumLag`. The k3d stack tests chart 0.29.1 of the operator, which is operator 1.30.1.

## Install

```bash
helm install data-proxy \
  oci://ghcr.io/prefeitura-rio/charts/data-proxy \
  --version <chart-version> \
  --values my-values.yaml
```

See [`helm/values.yaml`](../../helm/values.yaml) for all values.

## Chart tests

```bash
devenv --profile default tasks run test:chart
```

The task runs Helm lint, Helm unit tests, and Kubeconform.

## Images

The chart pins repository images in `helm/values.yaml`. Released chart values don't use `latest` for Data Proxy images.

## Proxy

Configure the OpenResty caching proxy under `proxy`. Values include `cacheTtl`, `cacheRedisDb`, `maxCacheBodyBytes`, `maxInflight`, `configUrl`, and `configRefreshSeconds`.

The chart passes these values to the proxy Deployment as `PROXY_*` environment variables. The proxy connects to Redis directly. See [Proxy](../proxy.md) for request flow and cache behavior.

## Serving values

These values tune the serving layer. The defaults are in [`helm/values.yaml`](../../helm/values.yaml). The columns of the tables are the value, its default, and what it does.

### PostgREST

| Value | Default | Effect |
| --- | --- | --- |
| `postgrest.maxRows` | `1000` | The most rows that PostgREST returns for one request. |
| `postgrest.aggregatesEnabled` | `true` | Allows `avg()`, `count()`, `max()`, `min()` and `sum()` in `select`. |
| `postgrest.dbPool` | `24` | The backend connections that each PostgREST pod opens. |
| `postgrest.dbPoolAcquisitionTimeout` | `3` | Seconds that a request waits for a backend connection before it fails. |
| `postgrest.dbPoolMaxIdleTime` | `300` | Seconds before PostgREST closes an idle backend connection. |
| `postgrest.preStopSleepSeconds` | `5` | Seconds that a stopping pod waits before it closes its port. The mesh needs this time to stop sending requests. |
| `postgrest.jwksUri` | empty | The address of the JSON Web Key Set (JWKS) that checks the JSON Web Token (JWT) signature. |
| `postgrest.jwtSecret` | empty | A shared HS256 secret. When set, it replaces `jwksUri`. |
| `postgrest.proxyUri` | empty | The public address that PostgREST and the Swagger UI show in the API description. |

### PgDog and Redis

| Value | Default | Effect |
| --- | --- | --- |
| `pgdog.poolSize` | `24` | The connections that one PgDog pod opens to the primary, and as many to the standbys. Keep `pgdog.replicas` × `poolSize` well below `max_connections`. |
| `redis.host`, `redis.port` | required, `6379` | The Redis address that the proxy uses for its cache. |
| `redis.readKey`, `redis.writeKey`, `redis.passwordKey` | `REDIS_READ`, `REDIS_WRITE`, `REDIS_PASSWORD` | The key names in the Secret `redis.existingSecret` that the sync workers read. |
| `redis.writerAddress`, `redis.writerHost`, `redis.writerPort`, `redis.readerHost`, `redis.readerPort` | `redis-writer:6379`, `redis-writer`, `6379`, `redis-reader`, `6379` | The addresses that the chart writes into the Redis Secret. It makes the Secret only when `redis.existingSecret` is empty. The reader falls back to the writer. |

### Proxy, mesh and service account

| Value | Default | Effect |
| --- | --- | --- |
| `proxy.largeClientHeaderBuffers` | `4 64k` | The number and size of the buffers for a large request header, as in the nginx directive `large_client_header_buffers`. |
| `ingress.sidecarInject` | `true` | Adds the Istio sidecar label to the console and notebooks pods. |
| `serviceAccount.create`, `serviceAccount.name` | `true`, `data-proxy` | Makes the ServiceAccount of the sync pods, or names an existing one. |
| `serviceAccount.annotations` | empty | Annotations for the ServiceAccount, such as the Workload Identity annotation for BigQuery. |

## Versioning

Images and the chart have separate versions. Each push to `main` that changes an image publishes it with the short commit hash as the tag, for example `data-proxy-sync:caa0da9`. The PostgreSQL image adds the prefix `17-`.

A chart release pins the newest tag of each image in `helm/values.yaml`. A released chart doesn't use `latest` for a repository image.

The chart version comes from the Conventional Commits since the previous release. A `feat` raises the minor version, a `fix` raises the patch version, and a `!` raises the major version. Don't change `helm/Chart.yaml` by hand. See [CI pipeline](../development/ci.md) and [Chart releases](../development/releases.md).

---

<!-- nav:start -->
---

[Previous: Operations](index.md) | [Next: Initialization](initialization.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
