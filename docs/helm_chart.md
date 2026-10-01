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
devenv --profile default tasks run dp:test:helm
```

The task runs Helm lint, Helm unit tests, and Kubeconform for standalone and HA values.

## Images

The chart pins repository images in `helm/values.yaml`. Released chart values don't use `latest` for Data Proxy images.

## Database storage

CNPG creates one retained PostgreSQL PVC per instance. Configure the size under `cnpg.storage.size`; Kubernetes doesn't support PVC size reduction. Remove retained PVCs only as a separate destructive operation.

SeaweedFS stores Parquet data in its own configured storage. The PostgreSQL database uses pg_duckdb to read Parquet; pg_duckdb is an extension, not a separate database service.

## Database initialization and readiness

The init-db callback waits for its CNPG writer and runs idempotent reconciliation with `ON_ERROR_STOP=1`. CNPG owns core database roles and memberships. Init-db owns extensions, schemas, tables, functions, RLS policies, and database S3 secrets.

PostgREST-ro and PostgREST-rw use HTTP readiness probes on `/`. A Deployment isn't Ready until PostgREST is serving its configured schema.

## Istio ingress

Set `ingress.enabled` to create the VirtualService. Set `ingress.auth.enabled` to create JWT authentication and authorization resources. Create the configured Gateway before installing the chart.

## Proxy

Configure the caching proxy under `proxy`. Values include `cacheTtl`, `fetchBufferSize`, `fetchTimeout`, `cacheRedisDb`, and `maxCacheBodyBytes`. See [Proxy](proxy.md) for request flow and cache behavior.

## Single and HA mode

`ha.enabled` is the only HA setting. It is `false` by default. The autoscaling blocks and the resources in the values file decide everything else.

| Part       | Single mode                                                                     | HA mode                                                                                                    |
| ---------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| PostgreSQL | One instance. `cnpg.instances` sets the count.                                  | One CNPG Cluster. `cnpg.autoscaling` scales it from 3 instances up. CNPG promotes a standby on failure.    |
| Pooler     | One `rw` Pooler. `cnpg.pooler.autoscaling` scales it.                           | A fixed `rw` Pooler (`cnpg.pooler.instances`) and a `ro` Pooler that `cnpg.pooler.autoscaling` scales.     |
| PostgREST  | One Deployment for reads and writes. `postgrest.autoscaling` scales it.         | A fixed `rw` Deployment (`postgrest.replicas`) for writes and `postgrest-ro` for reads, scaled by `postgrest.autoscaling`. |
| Proxy      | Sends every request to PostgREST.                                               | Sends `GET` and `HEAD` to `postgrest-ro` and all other methods to `postgrest`.                             |

The chart sets `HA_MODE` in the proxy Deployment from `ha.enabled`. The proxy routes by method only when `HA_MODE` is `true`. In HA mode, a read that finds `postgrest-ro` unreachable, or answered with 502, 503, or 504, is retried once on `postgrest`, which the primary backs. A write is never retried. This keeps reads working while the standbys start after a switch to HA mode, and during a failover.

To change the mode, run one upgrade in either direction:

```sh
helm upgrade data-proxy helm --set ha.enabled=true
helm upgrade data-proxy helm --set ha.enabled=false
```

Only the read-write database holds state, so nothing else needs a migration. A scaled-down standby loses its volume. The primary keeps its data.

KEDA owns every workload count. Helm never sets `replicas` or `instances` on a workload, so an upgrade cannot conflict with a scaler. A fixed workload gets a ScaledObject that KEDA pauses at the configured count. This is also how the single mode holds the Cluster at `cnpg.instances`.

Default triggers:

| Workload                  | Default trigger                                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Cluster (HA mode)         | Active PostgREST sessions per instance. The target comes from the CPU limit and the DuckDB threads.          |
| Pooler                    | One Pooler pod for each `cnpg.pooler.poolSize / 10` PostgREST pods. A PostgREST pod opens up to 10 connections. |
| PostgREST and nginx       | CPU and memory.                                                                                              |

Set `triggers` in an `autoscaling` block to replace a default. KEDA cannot scale a CNPG resource on CPU, because the resource has no pod selector.

Notes for HA mode:

- Every instance has the same `cnpg.resources`.
- The chart raises the instance floor to 3. Raise it more with `cnpg.autoscaling.minReplicaCount`.
- Replication is asynchronous, so a changed policy can reach a standby a moment later. To make `access_policy` writes wait until a standby applies them, set `cnpg.postgresql.synchronous` (for example `{method: any, number: 1, dataDurability: preferred}`) after the cluster has scaled up. CNPG rejects it on one instance, so it cannot be a default. A trigger then raises `synchronous_commit` to `remote_apply` for policy writes only, and other commits use `local`.
- The S3 secret is a file in the shared catalog volume (`duckdb-secrets`). Every instance mounts it, and only the primary writes it. After the first upgrade that adds this mount, run `helm upgrade` once more so `init-db` writes the secret to the volume.
- DuckDB reads the S3 secret once for each PostgreSQL backend. When the S3 keys change, `helm upgrade` rolls the poolers and the PostgREST pods, so every backend reloads the secret. Run `helm upgrade` a second time if a read happens between the roll and the `init-db` write.

```yaml
redis:
  existingSecret: data-proxy-redis
```

The Redis Secret contains a JSON value under `REDIS`:

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
