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
devenv tasks run dp:test:charts
```

The task runs Helm lint, Helm unit tests, and Kubeconform for standalone and HA values.

## Images

The chart pins repository images in `helm/values.yaml`. Released chart values do not use `latest` for Data Proxy images.

## Database storage

A fresh installation creates one retained PVC per pgduckdb member. Increase `pgduckdb.storage.size` when needed; Kubernetes does not support PVC size reduction. Remove retained PVCs only as a separate destructive operation.

Existing installations that use StatefulSet `volumeClaimTemplates` need a manual migration before using this storage layout.

## Database upgrades

Before PostgREST starts, the init container waits for its writer and runs idempotent reconciliation with `ON_ERROR_STOP=1`. A configuration checksum change also reruns reconciliation.

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

Add the following to your values file. HA members use the same `pgduckdb.image` as standalone members.

```yaml
ha:
  enabled: true
  schemas:
    bcadastro:
      members: 3
      storage:
        size: 80Gi
  patroni:
    replicationPassword: "<strong-password>"
```

## Versioning

Each repository image has an independent semantic version. Component Git tags start image releases:

- **`app-v1.0.0`**: Publishes `data-proxy:1.0.0`.
- **`postgres-v1.0.0`**: Publishes `data-proxy-postgres:1.0.0`.

The chart pins each image version in `helm/values.yaml`. A released chart does not use `latest` for a repository image.

The Helm pipeline increments the minor version on each release. Do not change `helm/Chart.yaml` by hand. A major version change means a breaking change.

---

[← Previous](environment_variables.md) · [Home](../README.md) · [Next →](metrics.md)
