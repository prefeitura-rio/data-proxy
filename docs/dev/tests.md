# Tests

`devenv --profile <component> test` runs every hook of the component in parallel. Each hook runs one lint or format-check task. Then `enterTest` runs the unit tests, which need no service, and the integration tests, which use the services of the component. The [repository style guide](https://github.com/prefeitura-rio/data-proxy/blob/main/STYLEGUIDE.md#tests) explains test design principles.

| Component | Unit tests (no service) | Integration tests (services) |
| --- | --- | --- |
| `sync` | `pytest tests/unit -m "not postgres"` | `pytest tests/integration` and `pytest tests/unit -m postgres`, with PostgreSQL and SeaweedFS |
| `proxy` | The Lua specs | The Hurl files, with OpenResty, Valkey and a mock PostgREST |
| `console` | Vitest | The browser tests, with PostgreSQL, SeaweedFS, Keycloak, PostgREST and the console |
| `chart` | The Helm chart tests | None |
| `tools` | Nushell setup regression tests in `scripts/tests/setup.nu` | None |

## Run the suites

```bash
devenv --profile proxy test    # one component
devenv --profile sync test
```

Run one suite at a time on one machine. The suites share `.devenv/test-state`, and two runs can pick the same port. CI runs each suite on its own runner.

devenv skips a task when none of its input files changed since the last green run, and a skipped task takes under a second. A failed run leaves no state, so the task runs again. A change in `sync/src` runs the sync suite and the console suite, because the console tests seed their data with the sync code. Use `devenv --profile <profile> tasks run --refresh-task-cache <task>` to force cached checks. Every input is a file or a glob over files, because devenv compares a folder by its metadata, and a fresh checkout changes every folder. `utils.mkInputs` throws for a folder, and `utils.mkGlobs` throws for a file with an unlisted extension.

## The test stack

`devenv test` starts the processes, waits for their readiness checks and setup tasks, then runs `enterTest`. Console setup uses one sequential task, `console:setup:stack`, after PostgreSQL and object storage become ready. It initializes databases, storage, roles, DBOS, migrations, rules, provisioning and fixtures, in that order. The console, PostgREST and control worker wait for this task. A failed command stops setup before dependent services start.

| Profile | Processes |
| --- | --- |
| `sync` | PostgreSQL in Podman (the image of `Containerfile.postgres`), SeaweedFS |
| `proxy` | Four OpenResty instances with a mock PostgREST each, and Valkey. The proxies start one after another, because each `lx exec` rewrites `proxy/lux.lock`. |
| `console` | The `sync` processes, Keycloak (the test image of `Containerfile.kc`), PostgREST, the console with its gateway, and a worker for the `control` queue |

Every process asks devenv for its port with `ports.<name>.allocate`. A port keeps its base number when it's free and moves up when it's busy. The setup passes the real number to the tests in environment variables. The base numbers live on the processes in component profiles and `nix/services/`. PostgreSQL starts empty on every run. `devenv --profile sync tasks run s3:clean:data` empties the object store.

`scripts/db.nu` prepares migrations, RBAC and persistent DuckDB secrets before test connections. It clears workflows queued during initialization before tests begin. DuckDB loads persistent secrets once per backend, so setup creates them before test queries.

Install Podman, or install Docker and set `CONTAINER_RUNTIME=docker`. The first PostgreSQL build is slow, and the engine keeps the layers. Test images use content-derived tags, and their tasks check image presence each time. Removing an image therefore triggers a rebuild even when task state remains cached. The image installs the `ducklake` extension while it builds, and an old layer fails the DuckLake tests. Refresh it with `podman build --no-cache`. To add a test container, declare a process with `utils.mkProcess { command = utils.podmanRun { ... }; }`, as `nix/services/postgres.nix` does.

## Setup script tests

`devenv --profile tools test` runs the tools checks and the setup regression tests. The tests cover image selection, independent component versions, registry compatibility, publication gates, CI output files and template cleanup. Temporary Git repositories and command stubs isolate release tests from publication services. The tests make no deployment or publication requests. Integrity fixtures verify that ordinary code passes while migration, template, reserved-name and pinned-input violations fail. Process-contract tests distinguish advisory findings from syntax, configuration and tool failures.

## Sync tests

The suite runs with `pytest-xdist` on up to four workers. Each worker clones its own database from `test_template`. A setup that every worker runs takes a file lock (`one_worker_at_a_time` in `sync/tests/helpers/core.py`). A new test uses only its own schema, so it runs on any worker.

- **Unit tests** check one function or template with every input. The template tests are in `sync/tests/unit/test_templates_*.py`, one file for each family: `duckdb`, `routing`, `filter`, `serving`, `access`, `maintenance` and `roles`. All but `duckdb` run on the real test server. The `rbac` tests also use Hypothesis, with 300 examples (`RBAC_EXAMPLES` sets another number).
- **Console SQL tests** build the console from the real migrations and templates with the fixture `console_api`. They cover the functions, the views, the import and the migrations on older data. `proxy/tests/unit/fixtures/console_config.json` is the document that `console.config()` writes, and a pytest case and the Lua specs both check it.
- **Integration tests** (`sync/tests/integration/`) check behavior across units. `test_pipeline.py` runs the body of `run_sync` with its real steps, a fake source and the real PostgreSQL, S3 and DuckLake.

These tests don't cover the API schemas, the database roles or what a token may do. The end-to-end suite covers those.

## Browser and proxy tests

`devenv --profile console test` runs the `playwright-bdd` features in `console/ui/tests/features/`: 94 scenarios in about 30 seconds, after about 25 seconds for the processes. The header of `console/ui/playwright.config.ts` explains the projects and the tags. Set `PLAYWRIGHT_GREP` to run some scenarios.

The notebook site needs a Chromium for the export of marimo. The notebooks pin the same Playwright version as the console, so the `console` shell has the matching headless shell, and `nu console/notebooks/build.nu notebooks/query.py site` works in it. `Containerfile.console` uses the Playwright image of the same version. marimo starts Chromium without a path option, so a Chromium of your system needs a link in the folder layout that Playwright expects under `PLAYWRIGHT_BROWSERS_PATH`. Keep that link outside the repository.

The proxy unit specs run with `nu scripts/lux.nu test`. The Hurl files in `proxy/tests/integration/` run through `devenv --profile proxy test` against the four proxies.

## End-to-end and load tests

The k6 suites need the k3d cluster, so CI doesn't run them. Run `cluster k6 e2e` before you merge a change to a permission, a role, an access policy or a view. The headers of `k6/e2e.js` and `k6/load.js` describe the scenarios, the log events, the traffic and the gates.

Upgrade verification waits until old sync pods stop. An old worker could otherwise consume a queued run against the new database schema.

| Command | Work |
| --- | --- |
| `cluster k6 e2e` | Validates the deployed release. `--build` builds fresh images first, and `--scenarios a,b` runs a subset. |
| `cluster k6 smoke` | One virtual user for 40 seconds. |
| `cluster k6 load` | Ramps to `K6_PEAK_RATE` and has it. `--capacity` widens the gates. |

## Coverage

Each suite prints the lines that no test runs. There is no minimum. Read the report to find a branch of your own code that no test reaches.

| Suite | Command |
| --- | --- |
| `sync` | `PYTEST_ADDOPTS='--cov=sync --cov-branch --cov-report=term-missing:skip-covered' devenv --profile sync test` |
| Console | `pnpm --dir console/ui coverage` |
| Proxy | `nu scripts/lux.nu coverage` |
