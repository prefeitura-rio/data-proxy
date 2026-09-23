# Local Development

The local stack runs on Minikube with Podman and Helm.

## Prerequisites

Install Minikube, Podman, `kubectl`, Helm, and Google Cloud CLI when using BigQuery.

```bash
devenv --profile default shell
cluster up
```

The script writes credentials to the ignored repository `.kubeconfig`. It does not change the user kubeconfig.

Installed services include KEDA, k6, Istio, SeaweedFS, OIDC, PostgreSQL with the pg_duckdb extension, Valkey, PostgREST, and Data Proxy.

```bash
cluster
```

## CI chart releases

CI uses `scripts/ci.nu` for repository logic. It calculates the next Helm version from Conventional Commit subjects since the previous `helm-v*` tag:

- breaking commits use the next major version;
- `feat` commits use the next minor version;
- other release commits use the next patch version.

Release-generated commits contain `[skip ci]`. Use `ci version` locally to inspect the calculated version without publishing a release.

## Seed BigQuery data

```bash
gcloud auth application-default login
seed --project rj-ia-desenvolvimento
```

Run one DBOS sync through the e2e trigger Job:

```bash
cluster k6 e2e
```

The standalone trigger is mounted at `/scripts` only in the temporary e2e Job; it is not part of the pipeline image.

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

| Command             | Purpose                                                        |
| ------------------- | -------------------------------------------------------------- |
| `cluster k6 e2e`    | Validates sync, RLS, fallback, cache, and local data.          |
| `cluster k6 migrate`| Runs shared → HA → shared migration validation.                |
| `cluster k6 load`   | Runs the normal load profile.                                  |
| `cluster k6 stress` | Runs the stepped stress profile.                               |

Run `cluster k6 e2e` after changing local images, sync configuration, fallback, SeaweedFS, or pg_duckdb behavior. Run `cluster k6 migrate` only after the baseline E2E is healthy. It recovers a failed transition, creates a shared baseline, validates both mode changes, and checks source-resource pruning.

## Development checks

Run the static and focused checks with devenv tasks:

```bash
devenv --profile default tasks run dp:lint dp:test
```

E2E and migration tests run against the local cluster or staging environment, not in CI.

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
