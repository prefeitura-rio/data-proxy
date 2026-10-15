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

The k3d configuration mounts `k3d/.tmp/catalogs` into every local node and Helmfile applies the local-only `k3d-rwx` static RWX storage manifest for the shared `data-proxy-duckdb` volume, which holds the DuckDB secrets.

The script writes the k3d credentials to the ignored `k3d/.kubeconfig`. It doesn't change the user kubeconfig.

Apply the current immutable local image tag without rebuilding images:

```bash
cluster sync
```

Use `cluster sync --build` to build and push fresh local images under a new immutable tag before applying them.

Installed services include KEDA, k6, Istio, SeaweedFS, OIDC, PostgreSQL with the pg_duckdb extension, Valkey, PostgREST, and Data Proxy.

```bash
cluster
```

## Tests

Run the unit tests that need no services with `devenv --profile default tasks run test:all`. The command runs the proxy specs, the chart tests, and the sync unit tests (`test:sync`).

Run the two integration suites with separate commands. Each command starts only the processes that its suite needs, runs the tests, and stops the processes:

```bash
devenv --profile sync test    # PostgreSQL, PostgREST, Silo, Valkey, then pytest
devenv --profile proxy test   # OpenResty with a mock PostgREST, Valkey, then the Hurl files
```

The `sync` profile starts these processes:

- PostgreSQL, built from `Containerfile.postgres` and run on the host network. Install Podman first, or install Docker and set `CONTAINER_RUNTIME=docker`. The build needs network for the base image. The engine keeps the layer cache, so the first run is slow and later runs only check the layers. The image is `localhost/data-proxy-test-postgres:dev`.
- Silo for S3, Valkey, and PostgREST.

The `proxy` profile starts OpenResty with a mock PostgREST and Valkey. It needs no container engine.

The two suites use different ports, so they can run at the same time:

| Suite | Ports |
| --- | --- |
| `sync` | 13000 (PostgREST), 15432 (PostgreSQL), 16379 (Valkey), 19000 and 19001 (Silo) |
| `proxy` | 18080 (proxy), 18081 and 18082 (mock PostgREST), 18090 to 18092 (proxy with Redis down), 18379 (Valkey) |

The `default` profile runs both suites in one `devenv test`. Both suites then share one Valkey on port 18379.

If the database setup in `scripts/db.nu` fails, PostgREST stops at once with the message `database bootstrap failed`, and the run ends in seconds. A marker file, `database-ready` in `.devenv/test`, tells PostgREST that the setup finished.

The PostgreSQL image installs the `ducklake` and `postgres` extensions while it builds. A cached build layer keeps the version that it first downloaded. The sync worker writes DuckLake 1.0 catalogs, and `ducklake_catalog` in `sync/tests/fixtures/db.py` writes the test catalog with the same DuckDB. An image with an older `ducklake` extension fails the DuckLake tests. Rebuild with `podman build --no-cache` to refresh the extension.

On the GitHub `ubuntu-latest` runner (image `20261004.327.1`), Podman is 4.9.3. A cold build of the test image takes about 35 s, and a cold `devenv --profile sync test` run takes about 60 s, so CI keeps no layer cache.

PostgreSQL starts empty on every run. The other services keep their files in `.devenv/test`.

To add another test container, declare `processes.<name> = mkPodmanProcess { ... }` with the function from `nix/lib.nix`, as `nix/processes/postgres.nix` does. Set `build.file` to build a Containerfile or `image` to run an existing image. Use `before` for shell lines that run before the container starts, and `ready` for a command that runs in the container. For a one-shot step after the container is ready, declare a normal process `after-<name>` with `after = [ "devenv:processes:<name>" ]`. The function uses the host Podman, or the engine in `CONTAINER_RUNTIME`.

## Python project

`sync/` is one flat Python project: `sync/pyproject.toml`, the lock file `sync/uv.lock`, the environment `sync/.venv`, the code in `sync/src/sync`, and the tests in `sync/tests`. `ruff`, `basedpyright`, `pyrefly`, `complexipy`, `vulture` and `pytest` all read their settings from that one `pyproject.toml`.

`uv sync` prepares the environment. The sync image runs `uv sync --frozen --no-dev --no-install-project` and sets `PYTHONPATH=/app/src`. The chart starts the worker with `python -m sync.dbos.main`.

## Console

`console/` is the admin web app. It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. The `console` profile provides Node, pnpm, TypeScript and `goose`:

```bash
devenv --profile console shell
cd console
pnpm install      # install the dependencies
pnpm dev          # start the dev server
pnpm check        # type-check the Svelte and TypeScript code
pnpm build        # build the Node server in console/build
```

`devenv --profile console tasks run lint:console` installs the locked dependencies and runs `pnpm check`. The `checks` profile includes it in `lint:all`.

## Database migrations

The migrations are plain SQL files in `console/migrations/`, and `goose` applies them. `goose` keeps its own version table, `public.goose_db_version`. They hold only what the console owns, in the `admin` schema: the sync configuration tables, the views that the console reads, and the functions that it calls, with the grants on them.

Everything else is a system function, and it belongs to the chart, in `helm/files/templates/postgres/`: the state, error and scope tables of the engine, the schemas of the synced data, their access policies and policy-writer roles, the pre-request function and its grants, the functions that reload PostgREST, the maintenance procedures, the DuckLake secrets, and the roles. The chart creates the state tables and DBOS creates its tables before `goose` runs, because some views read them.

The order is: the chart SQL for the state tables, then `dbos migrate`, then `goose up`. `scripts/db.nu` does this for the test database.

Set `PG_DATABASE_URL` and run the `migrate` command in the console shell:

```text
migrate up        # apply every new migration
migrate status    # list the applied migrations
migrate down      # undo the last migration
```

`devenv test` runs `migrate up` on the test database, so a broken migration fails the test setup. `lint:sql` checks the files with `sqlfluff` and `goose validate`.

To add a migration, run `goose -dir console/migrations create <name> sql`. Write both the `Up` and the `Down` part. Never edit a migration that a shared database has applied. Add a new one instead.

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

`token` prints a table with the token, its type, lifetime, scope, client, and realm. The local mode adds a port-forward command and a request example that uses `$TOKEN`. In Nushell, run `token | get token` to get only the token. Without options it asks the Keycloak chart in the local cluster for a `user` token. Use `--issuer` to ask any Keycloak realm directly:

```bash
token --issuer https://sso.example.com/realms/prod --client-id my-client --client-secret "$SECRET"
token --issuer https://sso.example.com/realms/prod --client-id my-client --username alice --password "$PASSWORD"
```

The client secret can also come from `KEYCLOAK_CLIENT_SECRET`. `--username` and `--password` switch to the password grant. `--scope` adds scopes.

See [Using the API](using.md).

## k6 commands

| Command             | Purpose                                                    |
| ------------------- | ---------------------------------------------------------- |
| `cluster k6 e2e`    | Validates the deployed release: sync, RLS, routing, cache, snapshots, and topology. |
| `cluster k6 e2e --build` | Builds and deploys fresh local images before validation. |
| `cluster k6 smoke`  | Runs one virtual user for 40 seconds to check the setup.  |
| `cluster k6 load`   | Ramps to a configurable peak and holds it. |

The load profile derives its rate from `K6_PEAK_RATE`, which defaults to 25 iterations per second. It ramps through 25%, 50%, 75%, and 100% of the peak, then holds the peak before ramping down. Because the arrival-rate executor counts iterations and a fallback pair makes two requests, the profile converts the target request rate into an iteration rate internally.

Use flags to control the same scenario. The command uses a 300-second production-shaped cache TTL by default and applies it to the deployed proxy before the run:

```nu
devenv shell -- nu scripts/cluster.nu k6 load \
  --peak-rate 100 \
  --cache-ttl 300 \
  --shares bottleneck=0.01 fallback=0.01
```

Use `--cache-ttl 5 --report-only` for the non-gating cold-cache stress run. Use `--capacity` with a higher peak and wider thresholds. The `bottleneck` share selects heavy DuckLake bottleneck cases; the `fallback` share selects configured-source fallback pairs. Staging capacity runs can use a higher peak from a generator outside the cluster host.

`cluster k6 smoke` and `cluster k6 load` wait until the Cluster has a primary and a standby, and PgDog and PostgREST are available. Before k6 starts, the command creates a one-off Job from the Helmfile-managed `manifests-sync-trigger` CronJob. This runs one normal DBOS sync to restore data that an earlier E2E run removed.

### Performance thresholds

A normal load run gates `checks`, HTTP failure rate, dropped iterations, cache latency, bounded DuckLake latency, fallback latency, and heavy/selective/pinned route latency. It always confirms `dropped_iterations == 0` so a run that can't start an iteration fails. The bounded DuckLake and cache trends carry a `p(99)` bound in addition to `p(95)`. Add `--report-only` when the client-risk route trends should be reported without gating the run. The flag sets `K6_GATE_CLIENT_RISK=false`; direct k6 runs can set that variable explicitly.

### Capacity runs need a separate generator

A single-node cluster can't measure its own capacity above a few hundred requests per second. The generator, PostgREST, PostgreSQL, PgDog, and the proxy share the node's cores, so the generator steals CPU from the system under test and the run reports a number that belongs to neither.

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

`cluster k6 e2e` verifies the existing release is ready, clears persisted test state, waits for the primary, the standby, PgDog, and PostgREST, then runs the full E2E suite. It doesn't rebuild images or force a CNPG image rollout. Use `cluster k6 e2e --build` after an image, sync configuration, fallback, SeaweedFS, or pg_duckdb change. The build form creates a new immutable image tag, applies Helmfile, and then runs the suite.

The suite also checks the topology: a primary and a standby stream, PgDog is available, no read-only PostgREST or CNPG Pooler is left, PostgREST reloaded without a restart, reads reach a standby, and the PostgREST listener sits on the primary.

## Development checks

Run the static and focused checks with devenv tasks:

```bash
devenv --profile default tasks run lint:all test:all
```

E2E tests run against the local cluster or staging environment, not in CI.

### Proxy rocks

The proxy uses [Lux](https://lux.lumen-labs.org/). `proxy/lux.toml` declares every Lua library and `proxy/lux.lock` pins them with source hashes. The Lua code lives in `proxy/src/proxy`, and the unit specs in `proxy/tests/unit`, one file for each module. The proxy request path against a real nginx, Redis, and a mock PostgREST is covered by the Hurl suite in `proxy/tests/integration/`; `devenv --profile proxy test` starts and stops those processes. The language server reads the OpenResty types that LuaLS bundles through `${3rd}` entries in `proxy/.luarc.json`. The Penlight types come from the `penlight-types` input in `devenv.yaml` (`goldenstein64/pl-definitions`). devenv exports them as `PENLIGHT_TYPES`, and `proxy/.luarc.json` lists `${env:PENLIGHT_TYPES}` as a library. The definition files have no module name, so cast each Penlight `require` to its module class (`pl.ClassModule`, `pl.ListClass`, `pl.tablex`, `pl.stringx`). Without the cast, LuaLS types the module from the rock source, which has no annotations, and hover shows `{ properties: unknown }` for `pl.class`. A `List(items)` built from a plain array has no item type for the language server, so annotate it if you need the type. The `luafilesystem` rock that Penlight needs is a C module and builds in the same image stage as `lpeg`. The LPeg types come from the `lpeg-types` input in `devenv.yaml` (`LuaCATS/lpeg`). devenv exports them as `LPEG_TYPES`, and `proxy/.luarc.json` lists `${env:LPEG_TYPES}` as a library. The `lpeg` rock is a C module: the image build stage installs a compiler and copies the built library to `/opt/data-proxy/lib`. `lint:proxy` runs luacheck (through `lx lint`) and the LuaLS type check with every rule that works for this code. Luacheck uses the `ngx_lua` globals, a 120-character line limit and a limit of 10 branches per function. LuaLS has every diagnostic group on, down to the hint level. `codestyle-check` is off because stylua formats the code, `spell-check` is off because it flags names such as `PostgREST`, and `name-style-check` and `no-unknown` are off because they reject conventions that the code uses on purpose. Lux installs `busted` for `lx test` and `stylua` and `luacheck` for `lx fmt` and `lx lint`, so Nix provides only LuaJIT, the language server for editors, and `lx`. `Containerfile.proxy` runs `lx build --only-deps` against the same lockfile, so the image gets the runtime rocks and no test rocks.

```bash
devenv tasks run proxy:rocks
```

The task installs the rocks into the ignored `proxy/.lux/` tree and fails when `lux.lock` changes. Change a version with `lx add` or `lx update` in `proxy/`, then commit the lockfile. Add a test library with `lx add --test`. `lint:proxy` runs `lx fmt`, `lx lint`, and the LuaLS type check, and fails when formatting changed a file. OpenResty bundles `resty.redis`, `resty.string`, `resty.lock`, and `cjson`, so only `lua-resty-http` is a runtime rock. Run the checks with `lint:proxy` and `test:proxy`.

### Nushell tests

The Helm suite tests pure helpers from `helm/files/lib.nu` without Kubernetes,
PostgreSQL, rclone, or network access:

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
