# Local Development

The local stack runs on Minikube with Podman and Helm.

## Prerequisites

Install Minikube, Podman, `kubectl`, Helm, and Google Cloud CLI when using BigQuery.

```bash
devenv shell
cluster up
```

The script writes credentials to the ignored repository `.kubeconfig`. It does not change the user kubeconfig.

Installed services include KEDA, k6, Istio, SeaweedFS, OIDC, PostgreSQL with pg_duckdb, Valkey, PostgREST, and Data Proxy.

```bash
cluster
```

## Seed BigQuery data

```bash
gcloud auth application-default login
seed --project rj-ia-desenvolvimento
```

Run one producer job when needed:

```bash
kubectl -n data-proxy create job \
  --from=cronjob/data-proxy-producer \
  data-proxy-producer-manual
```

## Access the API

Add this local host entry:

```text
127.0.0.1 data-proxy.local
```

```bash
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80
token
```

See [Using the API](using.md).

## k6 commands

| Command             | Purpose                                               |
| ------------------- | ----------------------------------------------------- |
| `cluster k6 e2e`    | Validates sync, RLS, fallback, cache, and local data. |
| `cluster k6 load`   | Runs the normal load profile.                         |
| `cluster k6 stress` | Runs the stepped stress profile.                      |

Run e2e after changing local images, sync configuration, fallback, SeaweedFS, or pg_duckdb behavior. Load and stress clear local state, run a sync, and wait for local tables before starting VUs.

## Stop the cluster

```bash
cluster down
```

If Helm installation fails, recreate the cluster:

```bash
cluster down
cluster up
```

---

[← Previous](backups.md) · [Home](../README.md)
