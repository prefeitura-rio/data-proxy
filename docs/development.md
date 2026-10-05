# Local Development

The local stack runs on k3d with Podman and Helm.

## Prerequisites

Install k3d, Podman, `kubectl`, Helm, and Google Cloud CLI when using BigQuery. Ensure the existing Podman network has DNS enabled and the Podman Docker-compatible socket variables are available in the shell that runs k3d.

```bash
devenv --profile default shell
cluster up
```

The k3d configuration mounts `.k3d/catalogs` into every local node and Helmfile applies the local-only `k3d-rwx` static RWX storage manifest for DuckLake catalogs.

The script writes the k3d credentials to the ignored repository `.kubeconfig`. It does not change the user kubeconfig.

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

| Command             | Purpose                                                    |
| ------------------- | ---------------------------------------------------------- |
| `cluster k6 e2e`    | Validates sync, RLS, routing, cache, snapshots, and mode. |
| `cluster k6 smoke`  | Runs one virtual user for 40 seconds to check the setup.  |
| `cluster k6 load`   | Holds the local peak of 100 req/s for 25 minutes.         |
| `cluster k6 stress` | Steps to 200 req/s to find the local breaking point.      |

Every profile derives its rate from one local peak, `K6_PEAK_RATE`, which defaults to 100 HTTP requests per second. Because the arrival-rate executor counts iterations and a BigQuery iteration makes two requests, the profile converts the request rate into an iteration rate internally. Override the peak with `K6_PEAK_RATE`, and the traffic mix with `K6_BIGQUERY_SHARE` and `K6_BOTTLENECK_SHARE` (both default to `0.05`). Staging capacity runs set a higher peak outside the local node.

`cluster k6 smoke`, `cluster k6 load`, and `cluster k6 stress` set the requested mode before the test: Helmfile applies `ha.enabled=true` with `--ha`, and `ha.enabled=false` without it. If the Cluster already targets that mode, the command skips the Helmfile sync and waits for readiness. Before k6 starts, the command creates a one-off Job from the Helmfile-managed `manifests-sync-trigger` CronJob. This runs one normal DBOS sync to restore data that an earlier E2E run removed. Tests leave the requested mode enabled when they finish. Compare a run with and without `--ha` to see what the read side adds.

### Performance thresholds

Each profile gates the stack-owned paths: `checks`, HTTP failure rate, dropped iterations, the cache, and bounded DuckLake reads. It always confirms `dropped_iterations == 0` so a run that cannot start an iteration fails. The bounded DuckLake and cache trends carry a `p(99)` bound in addition to `p(95)`, so a concurrent adversarial scan cannot hide behind the median.

`bigquery_duration_ms` and the `ducklake_heavy`, `ducklake_selective`, and `ducklake_pinned` trends are client risk: they measure the query a client chose, and an unbounded or heavily sorted request must not look like a stack regression. They are reported but do not gate a run. Set `K6_GATE_CLIENT_RISK=true` to fail on them anyway.

### Capacity runs need a separate generator

A single-node cluster cannot measure its own capacity above a few hundred requests per second. The generator, PostgREST, PostgreSQL, the pooler, and the proxy share the node's cores, so the generator steals CPU from the system under test and the run reports a number that belongs to neither.

Measure capacity with the generator outside the cluster host. The `default` devenv profile provides `k6`, and every address the suite needs comes from the environment:

```bash
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80 &
kubectl -n keycloak port-forward svc/keycloak 8080:8080 &

BASE_URL=http://localhost:3111 \
API_HOST=data-proxy.local \
OIDC_TOKEN_URL=http://localhost:8080/realms/dev/protocol/openid-connect/token \
K6_PROFILE=load K6_PEAK_RATE=500 \
k6 run k6/perf.ts --summary-export=summary.json
```

The suite reads the same `K6_PROFILE`, `K6_PEAK_RATE`, `K6_BIGQUERY_SHARE`, and `K6_BOTTLENECK_SHARE` variables as the in-cluster runner. Within the cluster, the runner pod now requests CPU and memory so a starved generator fails its own probes instead of silently shaping the result. A run that drops iterations fails the `dropped_iterations` threshold, which marks the measurement invalid rather than a capacity limit.

Run `cluster k6 e2e` after changing local images, sync configuration, fallback, SeaweedFS, or pg_duckdb behavior.

`cluster k6 e2e` verifies the existing release is ready, clears persisted test state, builds and pushes all local images, deploys single mode, then runs the full E2E suite and verifies the single-mode topology. `cluster k6 e2e --ha` follows the same sequence with HA mode. The command does not transition between modes; a later invocation selects and applies its own requested mode.

`MODE` in `k6/e2e.yaml` is `single` or `ha` and identifies the topology that the mode checks verify.

## Development checks

Run the static and focused checks with devenv tasks:

```bash
devenv --profile default tasks run dp:lint dp:test
```

E2E tests run against the local cluster or staging environment, not in CI.

### Nushell tests

The Helm suite tests pure helpers from `helm/files/lib.nu` without Kubernetes,
PostgreSQL, Litestream, rclone, or network access:

```bash
nu helm/files/tests/run.nu
```

The Helm Job scripts and local cluster scripts remain separate codebases. Do
not import modules across them.

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
