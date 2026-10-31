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
| `processes/console` (control worker) | `control-worker` runs the sync worker with `LISTEN_QUEUES=control,schedules`, so the browser tests run cancel, resume, the schedule actions and the schedule reconcile without a sync run |
| `processes/notebooks` (profile `notebooks`) | the task `notebooks:build` builds the site and `notebooks` serves it on its own origin. The `console` profile doesn't start it, because no browser test opens the site |

`enterTest` runs the `playwright-bdd` features in `console/ui/tests/features/`. The notebooks have no tests.

- The Vitest tests are the task `test:console`, which `test:all` runs.
- Set the variables of the tests in the `env` of the console profile.
- The browser tests don't start a sync run or wait for a provision Job, because both need Kubernetes. The end-to-end suite in the cluster (`k6`) tests the backend.
- The 86 scenarios run in about 25 seconds on half of the cores, after about 25 seconds for the processes to start. More workers don't help, because the machine is busy with the stack and the browsers. `playwright.config.ts` has three projects. The project `auth` signs each test user in once (`tests/setup/auth.setup.ts`) and saves the cookies in `test-results/.auth/`. The project `parallel` runs every scenario in parallel (`fullyParallel`). The project `shared` runs last, on one worker, and it has the scenarios that change data that every other scenario reads: the schedule, the token lifespan and the sync document (`tests/features/shared/`).
- Each scenario of `new-schema.feature` adds a schema of its own. The hook of the tag `@new-schema` gives the scenario a random suffix, and the step parameter `{name}` adds it to each schema and table name. A scenario and its repeats then run in parallel without a clash. The user `web_root` signs in, because its `schemas` claim is `*`. The hook after the scenario deletes the schemas that the scenario added, and no others. The tag `@import-ttl` restores the cache TTL of the `test` tables.
- The steps `I sign in as` and `I am signed in as` add the saved cookies of the user to the context of the scenario, so a scenario needs no round trip to the identity provider. A scenario with the tag `@fresh-login` signs in through the page, because it signs out or changes the token lifespan. `I am signed in as` opens no page, for a scenario that only sends requests.
- The layout sets `data-hydrated` on `<html>` when the page runs its scripts. A step that types or selects waits for it (`hydrated` in `tests/fixtures.ts`). Without it, a field that a step fills before the scripts run loses its value.
- The fixture `world` sets the refresh interval of the Sync pages to 2 seconds, so a scenario that waits for a live update takes about 2 seconds. The config asks the browser for reduced motion. The console and DaisyUI then turn their animations off, and Playwright doesn't wait for an animation to end before it selects.
- The test stack sets `GRANT_TTL_SECONDS` to 30.
- A step that needs SQL, such as the log line of the live-log scenario, runs `psql` on the address in `E2E_DATABASE_URL`.
- The scenario that shortens the token lifespan (`@short-token`) changes the client `data-proxy-console-web` in Keycloak through the admin API. A hook restores the old value.
- The profile sets `PLAYWRIGHT_BROWSERS_PATH` to the browsers of the Nix package set. `@playwright/test` and the Python `playwright` package therefore stay at the versions of that package.

To run some scenarios, set `PLAYWRIGHT_GREP`, for example `PLAYWRIGHT_GREP='health check' devenv --profile console test`. `PLAYWRIGHT_ARGS` passes other options to `playwright test`, such as `--fail-on-flaky-tests`. Keycloak starts in about 12 seconds and imports the realm at every start. `--repeat-each` doesn't suit the project `shared`, because it runs the copies on different workers. Run the command several times to look for a flaky scenario. The report of the last run is `console/ui/test-results/report.json`.

## Notebooks

`console/notebooks/` builds the static site of the query tab ([Notebooks](../notebooks.md)). `devenv --profile console tasks run lint:nb` checks and lints its TypeScript. The notebooks have no tests.

## Develop

The section [Console](#console) lists the commands of the `console` profile and of the tests. The migrations are in [Migrations](migrations.md).

See [Run the console locally](stack.md) to start the console in a browser.

<!-- nav:start -->
---

[Previous: Proxy rocks](rocks.md) | [Next: Run the console locally](stack.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
