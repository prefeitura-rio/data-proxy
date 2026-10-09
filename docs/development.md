# Local Development

The local stack runs on k3d with Podman and Helm.

## Prerequisites

Install k3d, Podman, `kubectl`, Helm, and Google Cloud CLI when using BigQuery. Ensure the existing Podman network has DNS enabled and the Podman Docker-compatible socket variables are available in the shell that runs k3d.

```bash
devenv --profile default shell
gcloud auth application-default login
cluster up
```

The local synchronization configuration uses BigQuery. `cluster up` stops before Helmfile when application-default credentials are absent.

The k3d configuration mounts `.k3d/catalogs` into every local node and Helmfile applies the local-only `k3d-rwx` static RWX storage manifest for the shared `data-proxy-duckdb` volume. Each PostgreSQL Pod gets an ephemeral catalog volume from the k3s `local-path` StorageClass.

The script writes the k3d credentials to the ignored repository `.kubeconfig`. It doesn't change the user kubeconfig.

Apply the current immutable local image tag without rebuilding images:

```bash
cluster sync
```

Use `cluster sync --build` to build and push fresh local images under a new immutable tag before applying them. Use `cluster sync --ha` to apply high-availability mode. Both flags work together.

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

The seed command creates every E2E fixture table, including the mutable snapshot fixture. Rerun it after a fixture schema change, then synchronize the local release:

```bash
gcloud auth application-default login
seed --project rj-ia-desenvolvimento
cluster sync
```

Run one DBOS sync through the E2E trigger Job:

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
| `cluster k6 e2e`    | Validates the deployed release: sync, RLS, routing, cache, snapshots, and mode. |
| `cluster k6 e2e --build` | Builds and deploys fresh local images before validation. |
| `cluster k6 smoke`  | Runs one virtual user for 40 seconds to check the setup.  |
| `cluster k6 load`   | Ramps to a configurable peak and holds it in single or HA mode. |

The load profile derives its rate from `K6_PEAK_RATE`, which defaults to 25 iterations per second. It ramps through 25%, 50%, 75%, and 100% of the peak, then holds the peak before ramping down. Because the arrival-rate executor counts iterations and a fallback pair makes two requests, the profile converts the target request rate into an iteration rate internally.

Use flags to control the same scenario. The command uses a 300-second production-shaped cache TTL by default and applies it to the deployed proxy before the run:

```nu
devenv shell -- nu scripts/cluster.nu k6 load \
  --peak-rate 100 \
  --cache-ttl 300 \
  --shares bottleneck=0.01 fallback=0.01
```

Use `--cache-ttl 5 --report-only` for the non-gating cold-cache stress run. Use `--capacity` with a higher peak and wider thresholds. The `bottleneck` share selects heavy DuckLake bottleneck cases; the `fallback` share selects configured-source fallback pairs. Staging capacity runs can use a higher peak from a generator outside the cluster host.

`cluster k6 smoke` and `cluster k6 load` set the requested mode before the test: Helmfile applies `ha.enabled=true` with `--ha`, and `ha.enabled=false` without it. If the Cluster already targets that mode, the command skips the Helmfile sync and waits for readiness. Before k6 starts, the command creates a one-off Job from the Helmfile-managed `manifests-sync-trigger` CronJob. This runs one normal DBOS sync to restore data that an earlier E2E run removed. Tests leave the requested mode enabled when they finish. Compare a run with and without `--ha` to see what the read side adds.

### Performance thresholds

A normal load run gates `checks`, HTTP failure rate, dropped iterations, cache latency, bounded DuckLake latency, fallback latency, and heavy/selective/pinned route latency. It always confirms `dropped_iterations == 0` so a run that can't start an iteration fails. The bounded DuckLake and cache trends carry a `p(99)` bound in addition to `p(95)`. Add `--report-only` when the client-risk route trends should be reported without gating the run. The flag sets `K6_GATE_CLIENT_RISK=false`; direct k6 runs can set that variable explicitly.

### Capacity runs need a separate generator

A single-node cluster can't measure its own capacity above a few hundred requests per second. The generator, PostgREST, PostgreSQL, the pooler, and the proxy share the node's cores, so the generator steals CPU from the system under test and the run reports a number that belongs to neither.

Measure capacity with the generator outside the cluster host. The `default` devenv profile provides `k6`, and every address the suite needs comes from the environment:

```bash
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80 &
kubectl -n keycloak port-forward svc/keycloak 8080:8080 &

BASE_URL=http://localhost:3111 \
API_HOST=data-proxy.local \
OIDC_TOKEN_URL=http://localhost:8080/realms/dev/protocol/openid-connect/token \
K6_PROFILE=load K6_PEAK_RATE=500 \
k6 run k6/load.ts --summary-export=summary.json
```

The suite reads the same `K6_PROFILE`, `K6_PEAK_RATE`, `K6_FALLBACK_SHARE`, and `K6_BOTTLENECK_SHARE` variables as the in-cluster runner. Within the cluster, the runner pod now requests CPU and memory so a starved generator fails its own probes instead of shaping the result without notice. A run that drops iterations fails the `dropped_iterations` threshold, which marks the measurement invalid rather than a capacity limit.

Run `cluster k6 e2e` after changing local images, sync configuration, fallback, SeaweedFS, or pg_duckdb behavior.

`cluster k6 e2e` verifies the existing release is ready, clears persisted test state, selects the requested mode with the current immutable image tag, then runs the full E2E suite. It doesn't rebuild images or force a CNPG image rollout. Use `cluster k6 e2e --build` after an image, sync configuration, fallback, SeaweedFS, or pg_duckdb change. The build form creates a new immutable image tag, applies Helmfile, and then runs the suite. `--ha` selects HA mode in either form.

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
