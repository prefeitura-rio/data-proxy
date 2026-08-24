# Helm Chart

## Prerequisites

The following components must be installed in the cluster before you deploy the chart:

- [KEDA](https://keda.sh/docs/latest/deploy/). This chart needs KEDA for the `ScaledJob` and `TriggerAuthentication` resources that drive the worker and the finalizer.

## Install

The chart publishes to `oci://ghcr.io/prefeitura-rio/charts`.

```bash
helm install data-proxy \
  oci://ghcr.io/prefeitura-rio/charts/data-proxy \
  --version <chart-version> \
  --values my-values.yaml
```

See [`helm/values.yaml`](../helm/values.yaml) for the full list of configuration options and their descriptions.

The default standalone image is `ghcr.io/prefeitura-rio/data-proxy-pgduckdb:1.0.0`. It contains pg_duckdb. It also contains PostGIS. A custom image must contain the `pg_duckdb` extension. It must also contain the `postgis` extension.

## Enable HA

Add the following to your values file and set `ha.patroni.image` to an image built from `Dockerfile.patroni`.

```yaml
ha:
  enabled: true
  patroni:
    image: ghcr.io/prefeitura-rio/data-proxy-patroni:1.0.0
    replicationPassword: "<strong-password>"
```

## Versioning

Each repository image has an independent semantic version. Component Git tags start image releases:

- **`app-v1.0.0`**: Publishes `data-proxy:1.0.0`.
- **`pgduckdb-v1.0.0`**: Publishes `data-proxy-pgduckdb:1.0.0`.
- **`patroni-v1.0.0`**: Publishes `data-proxy-patroni:1.0.0`.

The chart pins each image version in `helm/values.yaml`. A released chart does not use `latest` for a repository image.

The Helm pipeline increments the minor version on each release. Do not change `helm/Chart.yaml` by hand. A major version change means a breaking change.
