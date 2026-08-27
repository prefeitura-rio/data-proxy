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

## Database storage

A fresh installation creates one explicit PVC for each pgduckdb member. It creates one single-replica StatefulSet for each member. Standalone mode creates one member. HA mode creates the number of members in `ha.patroni.replicas`. Each member mounts its matching PVC.

Set `pgduckdb.storage.size` to the required capacity. A later increase updates the PVC. It does not change a StatefulSet claim template. Kubernetes does not support PVC size reduction.

The chart adds `helm.sh/resource-policy: keep` to each pgduckdb PVC. Helm keeps the database data during uninstall. Helm also keeps the data during HA scale-down. Remove a retained PVC only as a separate destructive operation.

This storage layout applies to fresh installations. The chart does not migrate installations that use StatefulSet `volumeClaimTemplates`. Migrate those installations separately before using this layout.

## Database upgrades

Before PostgREST starts, an init container waits for the PostgreSQL writer. It then runs the idempotent database reconciliation. The reconciliation updates roles, extensions, RLS tables, functions, triggers, policies, and each schema in `syncConfig.schemas`. In HA mode, the init container uses the Patroni master Service. In standalone mode, it uses the DuckDB Service.

The init container runs the scripts with `ON_ERROR_STOP=1`. PostgREST starts only when the scripts succeed. A sync configuration checksum change also starts the init container. PostgreSQL runs the initial setup for a new empty volume before the init container continues.

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
