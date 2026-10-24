# Frontend

The console and the notebooks are TypeScript projects. These sections show how to run, build and test them.

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

`devenv --profile console tasks run lint:console` installs the locked dependencies and runs `pnpm check` and `pnpm lint` in `console/ui/`. `lint:nb` runs `pnpm lint` in `console/notebooks/`. The root `.prettierignore` lists what Prettier skips, for the scripts and for the hook.

`pnpm lint` runs ESLint (the Svelte plugin and typescript-eslint) and `prettier --check`. prek runs the hooks `prettier` and `eslint` at commit time. They use the binaries of `console/ui/node_modules`, so run `pnpm install` in `console/ui/` once.

The [style guide](../../STYLEGUIDE.md#typescript-and-svelte) lists the rules. The `checks` profile includes `lint:console` in `lint:all`.

Run the tests of the console with `devenv --profile console test`. It works like `devenv --profile sync test`: devenv starts the services, the setup runs, and the tests run against them.

The profile in `console/devenv.nix` imports one module for each part of the setup. A module is a `.nix` file. Every part that runs as a process is in `nix/processes/`: the database, the object store, the identity provider, PostgREST, and the console. `nix/services/` contains the parts that devenv ships as a service module, which is only Redis today. `nix/utils.nix` contains the helpers that the modules share, and `nix/data.nix` contains the addresses and the test credentials:

| Process | Role |
|---|---|
| `processes/postgres` | the `pg_duckdb` image in Podman, as in the sync tests (the task `postgres:image` builds it, the process `postgres` runs it) |
| `processes/seaweedfs` | the object store, with its IAM API and a read-only reader key |
| `processes/keycloak` | the official Keycloak image in Podman, with the realm `dev` of `k3d/values/keycloak.yaml` and the redirect addresses of the local console |
| `processes/postgrest` | PostgREST, with the keys that the task `postgrest:jwks` downloads from the local Keycloak |
| `processes/console` (setup) | the tasks `console:setup:*`. Each task runs one step: the databases (`psql -f`), the bucket, `configure-db.nu`, the DBOS and console migrations, and `provision.nu` (the scripts of the chart Jobs). The last task runs `sync/tests/seeds/console.py`. It fills the `test` catalog with a table of 5000 rows and a delete file for 100 of them |
| `processes/console` (server and gateway) | the tasks `console:build:*` build the SvelteKit server and `console` runs it, behind `caddy reverse-proxy`, which sends `x-forwarded-proto` as Istio does |
| `processes/console` (control worker) | `control-worker` runs the sync worker with `LISTEN_QUEUES=control`, so the browser tests run cancel, resume and the schedule actions without a sync run |
| `processes/console` (notebooks) | the task `notebooks:build` builds the site and `notebooks` serves it on its own origin |

`enterTest` runs the `playwright-bdd` features in `console/ui/tests/features/` and then the browser test of `console/notebooks/`. The task `notebooks:check-site` runs first. It builds the variant of the site that the browser test opens (`dist-check`, with `--mode run`).

- The Vitest tests and the unit tests of the notebooks are the tasks `test:console` and `test:nb`, which `test:all` runs.
- Set `EXPECTED_ROWS_FILE` and the other variables in the `env` of the console profile.
- The browser tests don't start a sync run or wait for a provision Job, because both need Kubernetes. The end-to-end suite in the cluster (`k6`) tests the backend.
- The test stack sets `GRANT_TTL_SECONDS` to 30, so the scenario that renews the links takes about 40 seconds. The 83 scenarios run on four workers, one feature file for each worker, in about 50 seconds.
- Feature files run side by side, so two files must not change the same data. Only `config.feature` creates the schema `other`. A scenario that does it has the tag `@other-schema`, and a hook deletes the rows afterwards. The tag `@import-ttl` restores the cache TTL of the `test` tables.
- A step that needs SQL, such as the log line of the live-log scenario, runs `psql` on the address in `E2E_DATABASE_URL`.
- The scenario that shortens the token lifespan (`@short-token`) changes the client `data-proxy-console-web` in Keycloak through the admin API. A hook restores the old value.
- The profile sets `PLAYWRIGHT_BROWSERS_PATH` to the browsers of the Nix package set. `@playwright/test` and the Python `playwright` package therefore stay at the versions of that package.

To run some scenarios, set `PLAYWRIGHT_GREP`, for example `PLAYWRIGHT_GREP='health check' devenv --profile console test`. Keycloak imports the realm at every start, which takes about 40 seconds.

### Run the console locally

To use the console in a browser, start the same stack without the tests:

```bash
devenv --profile console up
```

Run the console in the background with `devenv --profile console up -d`, wait with `devenv processes wait`, and stop it with `devenv down`. The command first runs the builds of the console and the notebooks and shows their output. Then it starts the processes. The first start takes one to two minutes. A shell in the `console` profile runs the same builds, and later runs skip them until a source changes.

The console is at `http://console.localhost:13001/console`. When the gateway is ready, `up` opens that address in your browser, with `open` on macOS and `xdg-open` on Linux. The tests don't open it. If the port is busy, devenv uses the next free port and shows it in the process list.

Sign in as `admin` (role `dp_admin`, schema `test`, password `admin`). The tests use five more users with the password `web-test`:

- `web_admin`: role `dp_admin`, schema `test`.
- `web_viewer`: role `dp_console`.
- `web_api`: role `dp_api`, no console permission.
- `web_policy`: role `dp_policy`, the Access tab only.
- `web_other`: role `dp_admin`, schema `other`.

The seed adds 30 enqueued sync runs, two finished sync runs, 60 access policies, 15 errors and 32 history rows. It also adds two `test` tables with 5000 and 300 rows. The first finished sync run (`00000000-0000-4000-8000-0000000000f1`) has 61 tasks, 64 steps and 600 log lines. The second (`00000000-0000-4000-8000-0000000000f2`) has 5200 log lines, for the limit of 5000 lines.

No sync worker runs, so a sync run that you start stays `ENQUEUED`. The worker `control-worker` handles only the queue `control`, so cancel, resume and the schedule actions work.

## Notebooks

`console/notebooks/` builds the static site of the query tab ([Notebooks](../notebooks.md)). `devenv --profile console tasks run test:nb` runs its unit tests. The browser test of the built site runs in `devenv --profile console test`. To run it alone:

```bash
cd console/notebooks
nu build.nu notebooks/check.py dist-check --mode run
pnpm exec playwright test
```

The test serves the built site under the real policy, with a stub of the console on another origin. It checks four things:

- The policy blocks no request.
- The delete files apply (2700 of 3000 rows).
- The page ignores a message from another origin.
- Python gets at most 100000 rows.

## Develop

The section [Console](#console) lists the commands of the `console` profile and of the tests. The migrations are in [Migrations](migrations.md).

<!-- nav:start -->
---

[Previous: Checks](checks.md) | [Next: Nix setup](nix.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
