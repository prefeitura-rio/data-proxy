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

The k3d configuration mounts `k3d/.tmp/duckdb` into every local node and Helmfile applies the local-only `k3d-rwx` static RWX storage manifest for the shared `data-proxy-duckdb` volume, which holds the DuckDB secrets.

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
devenv --profile sync test    # PostgreSQL, SeaweedFS, then pytest
devenv --profile proxy test   # OpenResty with a mock PostgREST, Valkey, then the Hurl files
```

The `sync` profile starts these processes:

- PostgreSQL, built from `Containerfile.postgres` and run on the host network. Install Podman first, or install Docker and set `CONTAINER_RUNTIME=docker`. The build needs network for the base image. The engine keeps the layer cache, so the first run is slow and later runs only check the layers. The image is `localhost/data-proxy-test-postgres:dev`.
- SeaweedFS for S3.

The `sync` suite tests the sync code and the SQL templates. A unit test checks one function, class, method or template with every input and edge case. `sync/tests/unit/test_templates.py` holds all the template tests, as one class for each engine. The PostgreSQL class runs the templates on the real test server, the DuckDB class runs them on a real DuckDB with a file catalog, and the BigQuery class uses a mock. The `rbac` tests there include Hypothesis tests that generate random rules files and compare the answers of `rbac.can()` and `rbac.mine()` with a reference that the test computes in Python. A property runs 300 examples by default, and `RBAC_EXAMPLES` sets another number. Every template that has some logic has a test that renders it, runs it, and checks the result, and a test fails when a template has neither a test nor a reason on its list of skipped templates. An integration test (`sync/tests/integration/`) checks a behavior that crosses units, such as the catalog, the state and S3. The suite doesn't test the API schemas, the database roles, or what a token may do: the end-to-end suite tests those ([Console](console.md#how-roles-and-access-are-tested) lists what it covers).

The `proxy` profile starts OpenResty with a mock PostgREST and Valkey. It needs no container engine.

Every process asks devenv for its ports with `ports.<name>.allocate`. A port keeps its base number when it's free and moves up when it's busy, so two runs never clash and they can run at the same time. The setup passes the real number to the tests in environment variables.

| Suite | Base ports |
| --- | --- |
| `sync` | 15432 (PostgreSQL), 18333, 19333, 18082 and 18888 (SeaweedFS) |
| `proxy` | 18080, 18090, 18100 and 18110 (the four proxies), 18081, 18091, 18101 and 18111 (mock PostgREST), 18083, 18093, 18103 and 18113 (admin), 18399 (the Redis that the Redis-down proxy uses), 18379 (Valkey) |

The four proxies start one after another, because each `lx exec` rewrites `proxy/lux.lock` and two at the same time can read a half-written file.

The `default` profile runs both suites in one `devenv test`.

devenv runs the task `sync:setup` (`scripts/db.nu`) before it starts pytest. If the task fails, the run stops and shows the error of the task.

The PostgreSQL image installs the `ducklake` and `postgres` extensions while it builds. A cached build layer keeps the version that it first downloaded. The sync worker writes DuckLake 1.0 catalogs, and `ducklake_catalog` in `sync/tests/fixtures/db.py` writes the test catalog with the same DuckDB. An image with an older `ducklake` extension fails the DuckLake tests. Rebuild with `podman build --no-cache` to refresh the extension.

On the GitHub `ubuntu-latest` runner (image `20261004.327.1`), Podman is 4.9.3. A cold build of the test image takes about 35 s, and a cold `devenv --profile sync test` run takes about 60 s, so CI keeps no layer cache.

PostgreSQL starts empty on every run. The other services keep their files in `.devenv/test-state`.

To add another test container, declare `processes.<name> = utils.mkProcess { command = utils.podmanRun { ... }; } // { ready = ...; }`, as `nix/processes/postgres.nix` does. Build an image in a task that runs `before` the process, and set `ready` to a command that runs in the container. For a one-shot step after the container is ready, declare a task with `after = [ (utils.processTask "<name>") ]` and `wantedBy` the same process. `podmanRun` uses the host Podman.

## Python project

`sync/` is one flat Python project: `sync/pyproject.toml`, the lock file `sync/uv.lock`, the environment `sync/.venv`, the code in `sync/src/sync`, and the tests in `sync/tests`. `ruff`, `basedpyright`, `pyrefly`, `complexipy`, `vulture` and `pytest` all read their settings from that one `pyproject.toml`.

`uv sync` prepares the environment. The sync image runs `uv sync --frozen --no-dev --no-install-project` and sets `PYTHONPATH=/app/src`. The chart starts the worker with `python -m sync.dbos.main`.

## Console

`console/ui/` is the admin web app. It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. The `console` profile provides Node, pnpm, TypeScript and `goose`:

```bash
devenv --profile console shell
cd console/ui
pnpm install      # install the dependencies
pnpm dev          # start the dev server
pnpm check        # type-check the Svelte and TypeScript code
pnpm lint         # ESLint and the format check
pnpm format       # format the code with Prettier
pnpm build        # build the Node server in console/ui/build
```

`devenv --profile console tasks run lint:console` installs the locked dependencies and runs `pnpm check` and `pnpm lint` in `console/ui/`. `lint:nb` runs `pnpm lint` in `console/notebooks/`. The root `.prettierignore` lists what Prettier skips, for the scripts and for the hook. `pnpm lint` runs ESLint (the Svelte plugin and typescript-eslint) and `prettier --check`. prek runs the hooks `prettier` and `eslint` at commit time, with the binaries of `console/ui/node_modules`, so run `pnpm install` in `console/ui/` once. The [style guide](../STYLEGUIDE.md#typescript-and-svelte) lists the rules. The `checks` profile includes it in `lint:all`.

Run the tests of the console with `devenv --profile console test`. It works like `devenv --profile sync test`: devenv starts the services, the setup runs, and the tests run against them. The profile in `console/devenv.nix` imports one module for each part of the setup. Every part that runs as a process is in `nix/processes/`: the database, the object store, the identity provider and PostgREST, and also the console. `nix/services/` holds the parts that devenv ships as a service module, which is only Redis today. A module is a `.nix` file. `nix/utils.nix` holds the helpers that the modules share, and `nix/data.nix` holds the addresses and the test credentials:

| Process | Role |
|---|---|
| `processes/postgres` | the `pg_duckdb` image in Podman, as in the sync tests (the task `postgres:image` builds it, the process `postgres` runs it) |
| `processes/seaweedfs` | the object store, with its IAM API and a read-only reader key |
| `processes/keycloak` | the official Keycloak image in Podman, with the realm `dev` of `k3d/values/keycloak.yaml` and the redirect addresses of the local console |
| `processes/postgrest` | PostgREST, with the keys that the task `postgrest:jwks` downloads from the local Keycloak |
| `processes/console` (setup) | the tasks `console:setup:*`, one for each step: the databases (`psql -f`), the bucket, `configure-db.nu`, the DBOS and console migrations, `provision.nu` (the scripts of the chart Jobs), and `sync/tests/seeds/console.py`, which fills the `test` catalog with a table of 5000 rows and a delete file for 100 of them |
| `processes/console` (server and gateway) | the tasks `console:build:*` build the SvelteKit server and `console` runs it, behind `caddy reverse-proxy`, which sends `x-forwarded-proto` as Istio does |
| `processes/console` (notebooks) | the task `notebooks:build` builds the site and `notebooks` serves it on its own origin |

`enterTest` runs the `playwright-bdd` features in `console/ui/tests/features/` and then the browser test of `console/notebooks/`. The Vitest tests and the unit tests of the notebooks are the tasks `test:console` and `test:nb`, which `test:all` runs. Set `EXPECTED_ROWS_FILE` and the other variables in the `env` of the console profile. The browser tests don't start a sync run or wait for a provision Job, because both need Kubernetes. The end-to-end suite in the cluster (`k6`) tests the backend. The scenario that renews the links waits for the real timer of four minutes, so a full run takes about ten minutes. The profile sets `PLAYWRIGHT_BROWSERS_PATH` to the browsers of the Nix package set, so `@playwright/test` and the Python `playwright` package stay at the versions of that package.

To run some scenarios, set `PLAYWRIGHT_GREP`, for example `PLAYWRIGHT_GREP='health check' devenv --profile console test`. Keycloak imports the realm at every start, which takes about 40 seconds.

## Notebooks

`console/notebooks/` builds the static site of the query tab ([Notebooks](notebooks.md)). `devenv --profile console tasks run test:nb` runs its unit tests. The browser test of the built site runs in `devenv --profile console test`. To run it alone:

```bash
cd console/notebooks
nu build.nu notebooks/check.py dist-check --mode run
pnpm exec playwright test
```

The test serves the built site under the real policy, with a stub of the console on another origin, and checks that nothing is blocked, that the delete files apply (2700 of 3000 rows), that a message from another origin is ignored, and that Python gets at most 100000 rows.

## Nix setup

The root `devenv.yaml` composes the environment with `imports:`. Each component folder (`proxy/`, `sync/`, `console/` and `helm/`) has a `devenv.nix` with the profile of that component. The profile names the modules of a test setup and the tests, and nothing else. `proxy/devenv.yaml` holds the inputs that only the proxy uses, and devenv merges them. `nix/profiles/` holds the profiles that belong to no component: `base`, `checks`, `default` and `python`. Run a setup from the root with `devenv --profile <name>`.

The `nix/` folder holds the shared parts. A module in `nix/processes/` or `nix/services/` holds one component. `nix/utils.nix` and `nix/data.nix` hold the helpers and the shared values. The `base` profile gives every module the argument `utils`. Every script is Nushell, except a bash script that's a list of plain commands, such as `enterTest`.

A long-running service is a process, and a command that runs and exits is a task. `utils.mkProcess` and `utils.mkTask` build the command and the variables, and the caller adds the other options with `//`. A task runs `before` a process that needs it, or `after` a process that it needs. A setup with several steps is a chain of tasks (`utils.mkTaskChain`), so a failure names the step:

```nix
processes.gateway = utils.whenTesting (
  utils.mkProcess {
    command = [ "caddy" "reverse-proxy" "--from" ":13001" "--to" "127.0.0.1:13004" ];
  }
  // {
    ready = utils.probes.http utils.urls.console;
    after = [ (utils.processTask "console") ];
  }
);
```

devenv runs the processes with its native manager. A process or a task that names another one in `after` starts when that one is ready or has succeeded. `devenv test` starts the processes, waits for their `ready` checks and their setup tasks, and then runs the tests. A process is always declared, and it starts only in `devenv test`. A module reads the port of another process with `utils.port "<process>" "<name>"`, and the shared logins and addresses come from `nix/data.nix`.

`execIfModified` skips a build task when its input files didn't change, so delete `console/ui/build` or `console/notebooks/site` to force a build. A file that Nix builds from values is a `files` entry: `sync/.env.test`, the SeaweedFS identities, the SQL that creates the databases and the four nginx files. They live in the state folder of the run (`utils.fileEntry`), except `sync/.env.test`, which devenv links into the project and git ignores. A file whose input is YAML, such as the Keycloak realm and the sync settings, stays a derivation, because Nix can't read YAML. A task that reads a generated file runs `after` `devenv:files`, because `devenv tasks run` doesn't create files by itself.

## Database migrations

The migrations are plain SQL files in `console/migrations/`, and `goose` applies them. `goose` keeps its own version table, `public.goose_db_version`. They hold the data schema of the console, in the `console` schema: the sync configuration tables with their integrity triggers and row policies, and the views that the console reads. A function belongs to a migration only when a table or a view of the console needs it to exist. The migrations call `rbac.can()` and `rbac.mine()`, which the `configure-db` job creates before `migrate-db` runs, and they call `data_proxy.sees()`, which the same job creates with the state tables. The migrations hold no grants, because the role of the token is a chart value. The provision job grants the rights on the `console` schema to that role.

The migrations changed before the first release, so an install from an earlier build needs a reset. Drop the old schema and the version table in the application database before the new migrations run: `DROP SCHEMA admin CASCADE; DROP TABLE public.goose_db_version;`. The provision job resets the PostgREST schema list on its own. On k3d, rebuild afterward with `devenv shell -- nu scripts/cluster.nu sync --build`.

Everything else belongs to the chart, in `helm/files/templates/postgres/`: the functions and triggers that the console calls (`create_console_*.sql`, which the provision job installs after `goose up`), the state, error and scope tables of the engine, the schemas of the synced data, their access policies, the pre-request function and its grants, the rights of the role `user` on the `console` schema, the functions that reload PostgREST, the maintenance procedures, the DuckLake secrets, and the roles. The chart creates the state tables and DBOS creates its tables before `goose` runs, because some views read them.

The order is: the chart SQL for the state tables, then `dbos migrate`, then `goose up`. `scripts/db.nu` doesn't run this order for the test database. It creates the databases, loads the first migration (the permission rules that every data schema calls), and installs the reload functions.

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
| `cluster k6 e2e`    | Validates the deployed release: sync, RLS, routing, cache, snapshots, topology, runs, schedule, new schemas, tokens, and failover. |
| `cluster k6 e2e --build` | Builds and deploys fresh local images before validation. |
| `cluster k6 e2e --scenarios cache,access` | Runs only the named scenarios. |
| `cluster k6 smoke`  | Runs one virtual user for 40 seconds to check the setup.  |
| `cluster k6 load`   | Ramps to a configurable peak and holds it. |

The load profile derives its rate from `K6_PEAK_RATE`, which defaults to 25 iterations per second. It ramps through 25%, 50%, 75%, and 100% of the peak, then holds the peak before ramping down. Because the arrival-rate executor counts iterations and a fallback pair makes two requests, the profile converts the target request rate into an iteration rate internally.

Every iteration is one of these, with real Keycloak tokens and no write:

| Share | Traffic | Path |
| --- | --- | --- |
| The rest (92%, or 84% in capacity mode) | Data reads of the roles `dp_api` (`api`, `api_no_policy`): row filters, aggregates, JSON filters, sorted scans, fallback partitions, selective lookups and reads at a pinned snapshot. | Gateway, proxy, PostgREST |
| 1% each (5% each in capacity mode) | A fallback pair (a read and its cached repeat), and a bottleneck case: heavy queries, lookups and pinned reads (`K6_FALLBACK_SHARE`, `K6_BOTTLENECK_SHARE`). | Gateway, proxy, PostgREST |
| 5% | Console reads, as the web app sends them with the tokens of `console` (70%) and `admin` (30%): `overview`, `runs`, `tasks`, `logs`, `errors`, `health`, `synced`, `schedules`, `queues`, `schemas`, `tables`, and `table_files` for the admin only (`K6_CONSOLE_SHARE`). | PostgREST, `Accept-Profile: console` |
| 1% | A read of `access_policy` with the token of `policy` (`K6_POLICY_SHARE`). | Gateway, proxy, PostgREST |

The setup reads every console route once with each client that may read it, and stops the run if one fails. The load test never changes the configuration, starts a run, or writes the policy. Only the setup seeds the policy rows that the data reads need. The console reads go to `POSTGREST_URL` directly, as the web app does, so a generator outside the cluster needs a port-forward to PostgREST and `POSTGREST_URL=http://localhost:3000`. The gates `console_duration_ms` (p95 under 1 s) and `policy_duration_ms` (p95 under 500 ms) are first guesses. Measure a run, then set them.

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
kubectl -n data-proxy port-forward svc/data-proxy-postgrest 3000:3000 &

BASE_URL=http://localhost:3111 \
API_HOST=data-proxy.local \
OIDC_TOKEN_URL=http://localhost:8080/realms/dev/protocol/openid-connect/token \
K6_PROFILE=load K6_PEAK_RATE=500 POSTGREST_URL=http://localhost:3000 \
k6 run k6/load.js --summary-export=summary.json
```

The suite reads the same `K6_PROFILE`, `K6_PEAK_RATE`, `K6_FALLBACK_SHARE`, and `K6_BOTTLENECK_SHARE` variables as the in-cluster runner. Within the cluster, the runner pod now requests CPU and memory so a starved generator fails its own probes instead of shaping the result without notice. A run that drops iterations fails the `dropped_iterations` threshold, which marks the measurement invalid rather than a capacity limit.

Run `cluster k6 e2e` after changing local images, sync configuration, fallback, SeaweedFS, or pg_duckdb behavior.

### E2E layout

`k6/e2e.js` is the entry point. It runs the steps of every scenario in the order of their `stage` number. Every helper is in `k6/lib/`, split by topic (`env`, `log`, `check`, `http`, `auth`, `wait`, `kubernetes`, `cluster`, `console`, `runs`, `policy`, `pipeline`, `rbac`, `loadtest`), and `k6/lib/index.js` only re-exports the modules. A scenario file in `k6/scenarios/` holds only its steps, and `k6/load.js` holds only its orchestration (profiles, thresholds and route choice) and its iteration functions. The exceptions are the two functions of `load.js` that write to the metrics that the file creates.

A scenario file exports `steps`, a list of `{ scenario, name, stage, run }`. The `stage` numbers leave gaps (10, 20, ...), so a new step fits between two others. To add a scenario, create `k6/scenarios/<name>.js` and add one import and one `...<name>.steps` line to `k6/scenarios/index.js`. The suite doesn't run a scenario that `scenarios/index.js` doesn't list.

Every check is named `scenario: description`, and one check asserts one condition. A scenario file gets `expect`, `expectEach` and `requirePrecondition` from `scenario("<name>")`. The helper adds the prefix, throws when a description already starts with the name of a scenario, and logs a warning at once when a check fails. A step that throws doesn't stop the run: the orchestration records a failed check for it and goes on with the next step. A later step can then fail only because the failed one left its state half done, so read the first failure first.

Every line that the k6 scripts write has the same shape: `scenario=<name> step=<step> event=<event>`, then more `key=value` fields. k6 adds `level` and `source=console` itself, and a script can't add fields of its own, so the fields sit inside `msg`. `info` and `warn` in `k6/lib/log.js` write the lines, and the helpers name the step for you. `load.js` writes `scenario=load`. The events are:

| Event | When | Fields |
| --- | --- | --- |
| `run_started`, `run_done`, `run_failed_checks` | The start and the end of `e2e.js`. The end lists the names of every failed check. | `scenarios`, `steps`, `count`, `checks` |
| `step_started`, `step_done`, `step_failed` | Around each step. A step that throws logs the error. | `seconds`, `error` |
| `check_failed` | At once, when a check fails. | `check`, and the last request that the suite sent before the check: `last_request`, `last_status`, `last_body` |
| `wait_started`, `waiting`, `wait_done`, `wait_timed_out` | A labelled `waitUntil`. `waiting` repeats every 30 seconds. | `label`, `timeout`, `elapsed`, `seconds` |
| `job_started`, `job_waiting`, `job_done`, `job_failed` | A Kubernetes Job. `job_waiting` repeats every 30 seconds. | `job`, `elapsed`, `seconds` |
| `kill_pod`, `reads_during_outage` | The resilience scenarios. | `pod`, `role`, `down`, `failed`, `samples` |
| `load_started`, `load_check_failed`, `load_check_failures_suppressed`, `autoscaling_observed` | `load.js`. It prints at most 25 failures for each virtual user, then says that it stopped (the metrics keep counting). The wait for the tables of the load test is a labelled wait. | `profile`, `executor`, `duration`, `route`, `reason`, `status`, `source`, `deployment`, `from`, `to` |

The last request in `check_failed` is the request that came before the check, so it can be another request than the one that the check is about. Use it as a hint. To follow a run, filter the log: `rg 'event=(step_|check_failed|wait_timed_out|job_failed)'`. The summary at the end of the log is the output of k6 itself. The `cluster k6` commands stream the log of the runner while the test runs.

The order of the steps is fixed. The steps change shared state: the snapshot, the sync pods, the partition manifests, and the access policy. A later step relies on what an earlier step left, so run the whole suite after you move a step. The `failover` scenario runs last, because it kills the primary.

The k6 files are plain JavaScript with JSDoc types. An editor checks them through `k6/tsconfig.json` (`allowJs` and `checkJs`), and nothing builds them. `Containerfile.k6` copies `k6/` into the k6 image, and the TestRun starts `/scripts/e2e.js` or `/scripts/load.js` from the image (`script.localFile`). `cluster k6 e2e`, `cluster k6 smoke` and `cluster k6 load` build and push the image, with a new tag, before each run, so a script change needs no other step. Use `--scenarios` with a comma-separated list to run a subset, such as `cluster k6 e2e --scenarios cache,access`. An unknown name fails before the run starts. A subset skips the steps in between, so a scenario can fail when it needs state that a skipped scenario leaves.

Run `lint:toml` (`devenv --profile checks tasks run lint:toml`) after a change in `rbac.toml`. It runs `taplo lint`, which checks the file against `rbac.schema.json`, and `taplo fmt --check`. The `taplo` language server in the `checks` profile gives the same checks in an editor.

The scenarios `schedule`, `runs`, `newschema`, `tokens`, `idp`, `standby`, `cachestore`, `metrics` and `failover` check the lifecycle and the resilience of the system: the scheduler starts a run, a run from the console completes, a new schema is served end to end (with the exact rows of its seeded table), the gateway refuses an expired token and a token for another audience, a user of the `idp` client (client roles and a user attribute, as the identity provider recipe says) reads data and the console, the reads survive the loss of a standby and of Valkey, PostgREST exports its pool metrics, and a failover of the primary leaves the system working. The `jobs` scenario also restores the latest backup into a scratch schema and compares it with the live policy. The `cache` scenario also checks that a policy write that skips the proxy makes a cached answer stale after a reload. The `roles` scenario also checks that a rerun of `configure-db` with other rules changes what a role may do at once, also for a cached answer, and that a token with the role of a database login that isn't a request role (`ops`, `serving`, `postgres`), an unknown role or no role gets no rights, that a `schemas` claim as a plain text or an empty list behaves, and that an unknown permission ends as an HTTP 500. Some of them need set-up that the older scenarios don't. `tokens` needs the Keycloak clients `expired` and `wrong_audience`, `idp` needs the `idp` client with its two users, `roles` needs the `role_*` and `schemas_*` clients, and `newschema` needs the BigQuery table `new_schema_table`. `newschema` also checks that provision drops what an editor removes: the schema of the database, the catalog schema and the files in S3. `roles` reruns `configure-db` from its suspended CronJob. Keycloak imports its realm only at the first start, so run `cluster down` and `cluster up` after the pull, and run `seed` once to create the table.

The suite doesn't run in CI, because it needs the k3d cluster. It's the only test of roles and access, so run it before you merge a change to a permission, a role, a policy, or a view.

`cluster k6 e2e` verifies the existing release is ready, clears persisted test state, waits for the primary, the standby, PgDog, and PostgREST, then runs the full E2E suite. It doesn't rebuild images or force a CNPG image rollout. Use `cluster k6 e2e --build` after an image, sync configuration, fallback, SeaweedFS, or pg_duckdb change. The build form creates a new immutable image tag, applies Helmfile, and then runs the suite.

The suite also checks the topology: a primary and a standby stream, PgDog is available, PostgREST reloaded without a restart, reads reach a standby, and the PostgREST listener sits on the primary.

The Keycloak test realm has one client for each role, and each client carries a fixed `role` claim: `api` and `api_no_policy` are `dp_api`, `policy` is `dp_policy`, `console` is `dp_console`, `admin` is `dp_admin`, and `admin_other` is `dp_admin` for another schema. The `schemas` claim of `admin` lists the schemas that the console scenarios create. Keycloak imports its realm only at the first start, so after a change to `k3d/values/keycloak.yaml` run `cluster down` and `cluster up` again.

The `roles` scenario is a table. The image build converts `rbac.toml` to `/scripts/rbac.json` with `tomljson`, and the scenario reads it with `open()`, so the matrix follows the file. For every role and every permission, one probe uses what the permission guards with the real token of the role, and the scenario expects the probe to succeed exactly when the closure of the rules holds the permission. It also checks that each token carries its role, that `console.permissions()` lists the closure, and, in the cluster, that `rbac.roles` holds the same closure. When you change `rbac.toml`, no scenario needs a new row, but a new permission needs a probe.

The `console` scenario checks the API of the console: it reads the overview, the runs, and the tasks of the schema, proves that a token for another schema reads nothing, starts a run as an admin, changes and restores the cache lifetime of a table, lists the Parquet files of a table, and checks that no role changes a queue or a schedule, because both are read-only. It also calls every view and helper function at every level, checks the status views against the synced schema and the blocked table, adds and removes a schema as the admin, and checks that a request without a token, or with the token of a data user, is refused.

## Release checks

Run these before a release. The e2e suite and the checks of the other suites aren't enough: they don't test an upgrade or the limits of the load profile.

| Command | What it proves |
| --- | --- |
| `cluster k6 e2e` | The whole system works, including the lifecycle and resilience scenarios. |
| `cluster k6 load` | The latency gates hold, and KEDA scales the proxy, PostgREST and PgDog under load. |
| `cluster upgrade --from <previous chart version>` | The chart upgrades from the previous release: the migrations apply, the rules and the logins exist, and the access scenarios pass. It needs a published chart, so it can run only after the first release. |

The suites don't cover traces, the metrics that the sync workers send through OpenTelemetry (k3d has no collector), and the in-flight limit of the proxy under load (the Hurl suite covers it with a mock).

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

`helm/files/` has three folders. `config/` holds the settings and `rbac.toml`, `scripts/` holds the Nushell jobs and their tests, and `templates/` holds the SQL templates.

The Helm suite tests pure helpers from `helm/files/scripts/lib.nu` without Kubernetes,
PostgreSQL, rclone, or network access:

```bash
devenv --profile checks tasks run test:nu
```

The task also runs the tests of `scripts/tests/lib.nu`, which cover the pure helpers of the local scripts.
`run.nu` takes the test files as arguments and finds every `test ...` command in them.

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
