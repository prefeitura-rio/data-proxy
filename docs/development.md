# Local Development

The local stack runs on Minikube with Podman and Helm.

## Prerequisites

Install Minikube, Podman, `kubectl`, Helm, and Google Cloud CLI when using BigQuery.

```bash
devenv --profile default shell
cluster up
```

The script writes credentials to the ignored repository `.kubeconfig`. It doesn't change the user kubeconfig.

Installed services include KEDA, k6, Istio, SeaweedFS, OIDC, PostgreSQL with the pg_duckdb extension, Valkey, PostgREST, and Data Proxy.

```bash
cluster
```

## Chart releases

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

The standalone trigger is mounted at `/scripts` only in the temporary e2e Job; isn't part of the sync image.

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

| Command             | Purpose                                                     |
| ------------------- | ----------------------------------------------------------- |
| `cluster k6 e2e`    | Validates sync, RLS, routing, cache, snapshots, and modes.  |
| `cluster k6 smoke`  | Runs one virtual user for 40 seconds to check the setup.    |
| `cluster k6 load`   | Runs the normal load profile.                               |
| `cluster k6 stress` | Runs the stepped stress profile.                            |

`cluster k6 smoke`, `cluster k6 load`, and `cluster k6 stress` always set the mode first: `helm upgrade --set ha.enabled=true` with `--ha`, and `ha.enabled=false` without it. A run therefore never depends on the mode that an earlier run left. A run with `--ha` switches back to single mode when it ends, even when it fails. Compare a run with and without `--ha` to see what the read side adds.

Run `cluster k6 e2e` after changing local images, sync configuration, fallback, SeaweedFS, or pg_duckdb behavior.

`cluster k6 e2e --mode` picks the suite:

| `--mode`        | Runs                                                                                           |
| --------------- | ---------------------------------------------------------------------------------------------- |
| `full` (default) | The main suite, then a switch to HA mode and back to single mode, with a check in each mode. |
| `e2e`           | The main suite only.                                                                           |
| `modes`         | The switch to HA mode and back, with a check in each mode.                                     |

The suite becomes the `SUITE` variable of `k6/e2e.yaml`. `MODE` (`single` or `ha`) names the mode that a `modes` check expects. Every suite ends in single mode.

## Development checks

Run the static and focused checks with devenv tasks:

```bash
devenv --profile default tasks run dp:lint dp:test
```

E2E tests run against the local cluster or staging environment, not in CI.

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
