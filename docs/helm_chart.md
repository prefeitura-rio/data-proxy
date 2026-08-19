# Helm Chart

## Prerequisites

The following components must be installed in the cluster before you deploy the chart:

- [KEDA](https://keda.sh/docs/latest/deploy/) — required for the `ScaledJob` and `TriggerAuthentication` resources that drive the worker and finalizer.

## Install

The chart publishes to `oci://ghcr.io/prefeitura-rio/charts`.

```bash
helm install data-proxy \
  oci://ghcr.io/prefeitura-rio/charts/data-proxy \
  --version <chart-version> \
  --values my-values.yaml
```

See [`helm/values.yaml`](../helm/values.yaml) for the full list of configuration options and their descriptions.

## Enable HA

Add the following to your values file and set `ha.patroni.image` to an image built from `Dockerfile.patroni`.

```yaml
ha:
  enabled: true
  patroni:
    image: ghcr.io/prefeitura-rio/data-proxy-patroni:latest
    replicationPassword: "<strong-password>"
```

## Versioning

The Helm pipeline increments the minor version on each release. Do not change `helm/Chart.yaml` manually. A major version change indicates a breaking change.
