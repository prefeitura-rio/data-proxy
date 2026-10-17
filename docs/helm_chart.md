# Helm Chart

## Prerequisites

Install [KEDA](https://keda.sh/docs/latest/deploy/). Install Istio when `ingress.enabled` is `true`.

## Install

```bash
helm install data-proxy \
  oci://ghcr.io/prefeitura-rio/charts/data-proxy \
  --version <chart-version> \
  --values my-values.yaml
```

See [`helm/values.yaml`](../helm/values.yaml) for all values.

## Chart tests

```bash
devenv --profile default tasks run test:chart
```

The task runs Helm lint, Helm unit tests, and Kubeconform.

## Images

The chart pins repository images in `helm/values.yaml`. Released chart values don't use `latest` for Data Proxy images.

## Database storage

CNPG creates one retained PostgreSQL PVC per instance. Configure the size under `cnpg.storage.size`; Kubernetes doesn't support PVC size reduction. Remove retained PVCs only as a separate destructive operation.

SeaweedFS stores Parquet data in its own configured storage. The PostgreSQL database uses pg_duckdb to read Parquet; pg_duckdb is an extension, not a separate database service.

## Database initialization and readiness

Three Jobs prepare the database, and each one waits for what it needs.

1. **`configure-db`** waits for its CNPG writer and runs idempotent SQL with `ON_ERROR_STOP=1`. It runs as `ops`. It owns the system objects: the request roles (`anon`, the `dp_*` roles of `rbac.toml`, and the `serving` login), the `rbac` schema, the state, error and scope tables of the engine, the pre-request function and its grants, the functions that reload PostgREST, and the maintenance procedures. It also writes the persistent DuckDB secrets, which only a superuser may write. CNPG owns two roles, `ops` and `duckdb`.
2. **`migrate-db`** runs in the sync image. It waits for the last of the state tables, then runs `dbos migrate` and `goose up`. It owns the `console` schema of the console. The image holds `goose` and the files of `console/migrations`.

3. **`provision`** waits for the `console` tables. It runs as a Job on every install and upgrade. It also exists as a suspended CronJob, `<release>-provision`, that a Job comes from when a schema changes (see [Run a Job on demand](#run-a-job-on-demand)). On install and upgrade it first installs the functions and triggers of the console, which need the tables and views of the migrations. It fills an empty configuration once from `sync.config`, and afterward the console owns the configuration. It grants the baseline role (`auth.userRole`, which every `dp_*` role is a member of) its rights on the `console` schema: the rights are wide, and each view, policy, and function asks for the permission of the token. For each schema in `console.schemas` it creates the PostgreSQL schema and its access policy, with row policies that ask for `read_policy` and `edit_policy`. It grants the anonymous role the right to call `console.config()`, the one function that the proxy reads. It sets `pgrst.db_schemas` on the `serving` login to the schemas plus `console`, in that order, because the first schema is the default profile of PostgREST. It tells PostgREST to reload only when that list changes: the database waits until every standby replayed the change, then sends a `NOTIFY` on the `pgrst` channel. A schema that an editor adds is served within seconds, and a schema that an editor removes is dropped: the files first, then the catalog, then the schema of the database. See [Console](console.md#add-and-remove-a-schema).

[Development](development.md#database-migrations) explains what belongs to the chart and what belongs to the migrations.

## Run a Job on demand

`configure-db` and `provision` each exist twice: as a Job named by the release revision, which Helm runs on every install and upgrade, and as a suspended CronJob, `<release>-configure-db` and `<release>-provision`. A suspended CronJob never runs on a clock. It holds the pod definition, and both objects get it from one helper, so they can't differ. Something that needs the work creates a Job from the CronJob:

| Context | Who creates the Job | What runs |
| --- | --- | --- |
| Install and upgrade | Helm | The per-revision Jobs, with every schema (`PROVISION_ALL=true`). |
| An editor adds or removes a schema in the console | The sync worker. A trigger on `console.schemas` queues the workflow `provision_schemas`. | `provision`: what's pending and what's removed. |
| The start of every sync run | The sync worker, with the same call | `provision`. A lost event or a drift heals here. |
| An operator, after a change of `rbac.toml` or a failed run | A person | `configure-db` or `provision`. |

The one-line command for a person:

```bash
kubectl -n data-proxy create job --from=cronjob/<release>-provision provision-by-hand
kubectl -n data-proxy create job --from=cronjob/<release>-configure-db configure-db-by-hand
```

The sync ServiceAccount may read the CronJobs and create Jobs in its namespace, and nothing more.

The PostgREST Deployment uses an HTTP readiness probe on `/`. It isn't Ready until PostgREST is serving its configured schema.

## Istio ingress

Set `ingress.enabled` to create the VirtualService. Set `ingress.auth.enabled` to create JWT authentication and authorization resources. Create the configured Gateway before installing the chart.

## Proxy

Configure the OpenResty caching proxy under `proxy`. Values include `cacheTtl`, `cacheRedisDb`, `maxCacheBodyBytes`, `maxInflight`, `configUrl`, and `configRefreshSeconds`. The chart passes these values to the proxy Deployment as `PROXY_*` environment variables. The proxy connects to Redis directly; Webdis isn't deployed. See [Proxy](proxy.md) for request flow and cache behavior.

## Topology

The chart always deploys the same pieces:

| Part       | Role                                                                                                                             |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL | One CNPG Cluster with a primary and at least one standby. `cnpg.autoscaling` adds standbys under load. CNPG promotes a standby on failure. |
| PgDog      | A pooler in front of PostgreSQL. It sends read-only transactions to the standbys and every other transaction to the primary. It runs `pgdog.replicas` pods (two by default), and no scaler. |
| PostgREST  | One Deployment that connects to PgDog. `postgrest.autoscaling` scales it.                                                        |
| Proxy      | Sends every request to PostgREST.                                                                                                |

PostgREST starts a read-only transaction for each `GET`, and a read/write transaction for each write and each function call. PgDog reads that option from the `BEGIN`. A `GET` goes to a standby, and a write goes to the primary. With one instance, or while no standby can serve, PgDog reads from the primary. PgDog pools transactions, so many PostgREST connections share a few PostgreSQL backends. The pool uses the `serving` login. The chart renders it into the `<release>-pgdog-users` Secret.

PgDog finds the standbys through the `-ro` Service of the cluster, so it sees them as one host, and Kubernetes picks the standby for each new connection. PgDog closes a server connection after about five minutes, so a new standby takes its share within that time. In a measured run the standbys served 50% and 50% of the reads, and in one of four runs 31% and 69%. CNPG takes a standby that's more than `cnpg.maxReplicaLag` behind out of the `-ro` Service until it catches up, and a standby that stops answering leaves it within about six seconds. PgDog doesn't measure the lag. Its time-based lag limit counts an idle primary as lag. A pool that can't connect gets no requests for 10 seconds, and the next read goes to the primary.

The PostgREST connection address lists PgDog first and the `-rw` Service second. PostgREST asks for a read/write session for its `LISTEN` connection. PgDog never gives one, so the listener connects to the primary. A `NOTIFY` on the primary then reaches PostgREST.

A read that follows a write can reach a standby that hasn't replayed the write. The lag stays below `cnpg.maxReplicaLag`, and normally it's a few milliseconds. A caller that needs the new row at once reads it from the answer of the write (`Prefer: return=representation`).

Only the primary database holds state. A scaled-down standby loses its volume. The primary keeps its data.

KEDA owns the count of every workload except PgDog. Helm sets only `pgdog.replicas`, because each extra PgDog pod opens its own pools and adds connections to PostgreSQL. Helm never sets `replicas` or `instances` on any other workload, so an upgrade can't conflict with a scaler.

Default triggers:

| Workload                  | Default trigger                                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Cluster                   | Active PostgREST sessions per instance. The target comes from the CPU limit and the DuckDB threads.          |
| PostgREST and nginx       | CPU and memory.                                                                                              |

Set `triggers` in an `autoscaling` block to replace a default. KEDA can't scale a CNPG resource on CPU, because the resource has no pod selector.

Notes for the cluster:

- Every instance has the same `cnpg.resources`.
- The chart raises the instance floor to 2 (one primary and one standby). Raise it more with `cnpg.autoscaling.minReplicaCount`.
- Replication is asynchronous, so a changed policy can reach a standby a moment later. To make `access_policy` writes wait until a standby applies them, set `cnpg.postgresql.synchronous` (for example `{method: any, number: 1, dataDurability: preferred}`) after the cluster has scaled up. CNPG rejects it on one instance, so it can't be a default. A trigger then raises `synchronous_commit` to `remote_apply` for policy writes only, and other commits use `local`.
- The DuckDB secrets (the S3 secret `s3`, and the catalog secrets `ducklake_catalog_pg` and `ducklake_catalog`) are files in `/duckdb/secrets` on the shared `<release>-duckdb` volume. The chart creates the volume when `ducklake.secrets.storage.storageClass` is set. Set it before you serve data, because the console adds schemas after the install and the chart can't know them. Every instance mounts that folder at `~/.duckdb/stored_secrets`, and only the primary writes it. The catalogs aren't on this volume: they live in the `ducklake` database, and every standby gets its own copy through replication. After the first upgrade that adds a secret, run `helm upgrade` once more so `configure-db` writes it to the volume.
- DuckDB reads the S3 secret once for each PostgreSQL backend. When the S3 keys change, `helm upgrade` rolls PgDog and the PostgREST pods, so every backend reloads the secret. Run `helm upgrade` a second time if a read happens between the roll and the `init-db` write.

```yaml
redis:
  existingSecret: data-proxy-redis
  host: redis-writer
  port: 6379
```

`host` and `port` are the one stable Redis endpoint the proxy uses. `host` has no default, and the chart fails to render without it. The operator provides it, with replication and failover behind it. The Redis Secret contains a JSON value under `REDIS` for the sync workers:

```json
{"read":"redis://reader:6379/1","write":"redis://writer:6379/0"}
```

Redis has no autoscaler unless custom triggers are supplied.

## Versioning

Each repository image has an independent semantic version. Component Git tags start image releases:

- **`app-v1.0.0`**: Publishes `data-proxy:1.0.0`.
- **`postgres-v1.0.0`**: Publishes `data-proxy-postgres:1.0.0`.

The chart pins each image version in `helm/values.yaml`. A released chart doesn't use `latest` for a repository image.

The Helm pipeline increments the minor version on each release. Don't change `helm/Chart.yaml` by hand. A major version change means a breaking change.

---

[← Previous](environment_variables.md) · [Home](../README.md) · [Next →](metrics.md)
