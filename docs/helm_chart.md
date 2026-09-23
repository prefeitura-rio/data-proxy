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

## BigQuery fallback

Enable fallback with:

```yaml
fallback:
  enabled: true
```

Configure proxy values under `fallback`, including `cacheTtl`, `fetchBufferSize`, `fetchTimeout`, `cacheRedisDb`, and `maxCacheBodyBytes`. See [Fallback](fallback.md) for request flow and cache behavior.

## Enable HA

HA creates one CNPG Cluster, read Pooler, PostgREST-ro/rw pair, and nginx Deployment for each schema in `syncConfig.schemas`. The `ha.schemas` list contains optional overrides only.

GET and HEAD requests use PostgREST-ro through the read Pooler. Mutations use PostgREST-rw directly against the current writer. CNPG manages PostgreSQL replication and failover; no Patroni or HAProxy resources are required.

```yaml
redis:
  existingSecret: data-proxy-redis

ha:
  enabled: true
  schemas:
    - name: bcadastro
      postgrest:
        triggers: []
      fallback:
        triggers: []
```

The Redis Secret contains a JSON value under `REDIS`:

```json
{"read":"redis://reader:6379/1","write":"redis://writer:6379/0"}
```

An empty PostgreSQL trigger list uses CPU. Empty PostgREST and nginx trigger lists use CPU and memory. Redis has no autoscaler unless custom triggers are supplied.

## Migrate between modes

Use two values files. Keep the shared local values first and the HA overlay second:

```sh
helm upgrade data-proxy ./helm \
  --namespace data-proxy \
  --values scripts/values/data-proxy.yaml \
  --values scripts/values/data-proxy-ha.yaml \
  --kubeconfig .kubeconfig \
  --kube-context data-proxy
```

The migration callback retains the source CNPG topology while Helm creates and initializes the target. It copies each configured schema with an idempotent dump/restore, waits for target initialization, and records `data-proxy-mode-state`. Run a normal reconciliation after the migration to prune retained source resources. Reverse the values-file order for HA to shared:

```sh
helm upgrade data-proxy ./helm \
  --namespace data-proxy \
  --values scripts/values/data-proxy.yaml \
  --kubeconfig .kubeconfig \
  --kube-context data-proxy
```

Don't delete CNPG or application resources during a transition. A errored release can be retried after the source state is inspected; preserve the source topology until the copy succeeds.

## Versioning

Each repository image has an independent semantic version. Component Git tags start image releases:

- **`app-v1.0.0`**: Publishes `data-proxy:1.0.0`.
- **`postgres-v1.0.0`**: Publishes `data-proxy-postgres:1.0.0`.

The chart pins each image version in `helm/values.yaml`. A released chart doesn't use `latest` for a repository image.

The Helm pipeline increments the minor version on each release. Don't change `helm/Chart.yaml` by hand. A major version change means a breaking change.

---

[← Previous](environment_variables.md) · [Home](../README.md) · [Next →](metrics.md)
