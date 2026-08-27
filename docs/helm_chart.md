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

A fresh installation creates one explicit PVC and one single-replica StatefulSet for each pgduckdb member. Standalone mode creates one member. HA mode creates `ha.patroni.replicas` members. Each member mounts only its matching PVC.

Set `pgduckdb.storage.size` to the required capacity. A later increase updates the PVC directly. It does not change an immutable StatefulSet claim template. Kubernetes does not support PVC size reduction.

The chart marks each pgduckdb PVC with `helm.sh/resource-policy: keep`. Helm does not delete database data during an uninstall or HA scale-down. Remove retained PVCs only as a separate destructive operation.

This storage layout applies to fresh installations. The chart does not automatically migrate installations that use StatefulSet `volumeClaimTemplates`. Those installations need a separate migration procedure before they use this layout.

## Database upgrades

Before every Helm upgrade, the chart runs an idempotent database reconciliation Job. The Job waits for the PostgreSQL writer. It updates roles, extensions, RLS tables, functions, triggers, policies, and every schema declared in `syncConfig.schemas`. In HA mode, it uses the Patroni master Service. In standalone mode, it uses the DuckDB Service.

The Job is a `pre-upgrade` Helm hook. Helm stops the upgrade if a `psql` command fails. The Job creates each new schema, its `freshness` table, grants, and RLS policy before PostgREST receives the updated schema list. For a new empty volume, PostgreSQL runs the initial database setup.

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
