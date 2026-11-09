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
| `-swagger-ui-schemas` | ConfigMap | The schema list of the Swagger UI. A sync worker keeps it current. |
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
devenv --profile default tasks run chart:test:unit
```

The task runs Helm lint, Helm unit tests and Kubeconform.

## Images

The chart pins repository images in `helm/values.yaml`. Released chart values don't use `latest` for Data Proxy images.

## Proxy

Configure the OpenResty caching proxy under `proxy`. Values include `cacheTtl`, `cacheRedisDb`, `maxCacheBodyBytes`, `maxInflight`, `configUrl` and `configRefreshSeconds`.

The chart passes these values to the proxy Deployment as `PROXY_*` environment variables. The proxy connects to Redis directly. See [Proxy](../proxy/index.md) for request flow and cache behavior.

## Sync values

These values tune the sync workers. The defaults are in [`helm/values.yaml`](../../helm/values.yaml). [KEDA Scaling](keda.md) describes the scale of the workers.

### DuckDB memory and spill

| Value | Default | Effect |
| --- | --- | --- |
| `sync.duckdb.memoryLimit` | `1GB` | The memory of each DuckDB connection of a sync worker. DuckDB spills to the disk beyond it. |
| `sync.duckdb.threads` | `2` | The threads of each DuckDB connection. |
| `sync.duckdb.tempDirectory` | `/tmp/duckdb` | The folder where DuckDB spills beyond `memoryLimit`. Each connection makes a folder of its own in it. |
| `sync.duckdb.tempVolume.enabled` | `false` | Mounts a volume at `tempDirectory`. When it's off, the spill uses the filesystem of the container, which is the disk of the node. |
| `sync.duckdb.tempVolume.type` | `ephemeral` | `ephemeral` makes a claim for each pod and deletes it with the pod. `emptyDir` uses the disk of the node. |
| `sync.duckdb.tempVolume.size` | `50Gi` | The size of the claim, or the size limit of the `emptyDir`. |
| `sync.duckdb.tempVolume.storageClassName` | empty | The StorageClass of the claim. Empty uses the default StorageClass. |

A DuckDB connection takes 80% of the memory of the pod and every thread of the node. A pod has one connection for each dump task and one for each publish. Without a limit, the connections of one pod fill the pod and Kubernetes kills it (`OOMKilled`).

`sync.duckdb.memoryLimit` limits each connection. Each connection has its own spill folder, because two connections in one folder overwrite the files of each other and DuckDB crashes. Keep `memoryLimit` times (`sync.dumpQueueWorkerConcurrency` + 2) below `sync.resources.limits.memory`. The 2 stands for a publish and a maintenance run. Four connections of 1 GB (two dumps, a publish and a maintenance run) use 4 GB of the 6 GiB of a pod.

A row group is in memory while DuckDB writes it. The default of 122880 rows needs more than 3 GB for a table with wide JSON columns. The dump writes row groups of 10000 rows. The publish writes row groups of 122880 rows and stops a row group at 32 MB. The limit in bytes, not the rows, bounds the memory of wide tables. The two widest tables took 1.1 GB with it, and they ran out of memory at 1 GB without it. A narrow table keeps the large row groups, which DuckDB reads about twice as fast as groups of 10000 rows.

#### Spill volume

Without a volume, the spill folder is on the filesystem of the container, which is the disk of the node. A large spill can fill that disk, and then Kubernetes evicts the pod.

Set `sync.duckdb.tempVolume.enabled` to give the folder a disk of its own. Use `type: ephemeral` with a fast `storageClassName` for a large spill: the chart makes a claim for each pod and deletes it with the pod. Use `type: emptyDir` to cap the use of the node disk with `size`.

A disk-backed volume belongs to root. The chart sets `fsGroup` on the sync pod to the group of the sync user, so the sync can write to the volume.

Don't mount a bucket through FUSE as the spill folder. Each connection writes and reads small blocks at random, and a bucket mount is slow for that access. Each connection also needs a folder of its own.

## Serving values

These values tune the serving layer. The defaults are in [`helm/values.yaml`](../../helm/values.yaml). The columns of the tables are the value, its default and what it does.

### PostgREST

| Value | Default | Effect |
| --- | --- | --- |
| `postgrest.maxRows` | `1000` | The most rows that PostgREST returns for one request. The sync worker gets it as `AGGREGATE_MAX_ROWS`, so an aggregate function gives at most as many rows. |
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
| `cnpg.duckdb.maxMemory` | `1GB` | The memory of each DuckDB backend of PostgreSQL. One heavy read took about 1.4 GiB, and six of them stopped a standby with a 6Gi limit. In the worst case, `pgdog.poolSize` × 1.4 GiB must fit `cnpg.resources.limits.memory`. |
| `redis.host`, `redis.port` | required, `6379` | The Redis address that the proxy uses for its cache. A host name with no dot gets the namespace of the release, because nginx doesn't use the search domains of the pod. |
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

Images and the chart have separate versions. Each push to `main` that changes an image publishes it with the short commit hash as the tag, for example `data-proxy-sync:caa0da9`. The PostgreSQL image adds the prefix `18-`.

A chart release pins the newest tag of each image in `helm/values.yaml`. A released chart doesn't use `latest` for a repository image.

The chart version comes from the Conventional Commits since the previous release. A `feat` raises the minor version, a `fix` raises the patch version and a `!` raises the major version. Don't change `helm/Chart.yaml` by hand. See [Chart releases](../dev/ci.md#chart-releases).

---

<!-- nav:start -->
---

[Previous: Operations](./index.md) | [Next: Initialization](./initialization.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
