# Tests

Each component has two test tasks. `<component>:test:unit` needs no service. `<component>:test:int` starts the services that it needs. The rules for a test are in [Style](../style.md#tests).

| Component | `:unit` (no service) | `:int` (services) |
| --- | --- | --- |
| `sync` | `pytest tests/unit -m "not postgres"` | `pytest tests/integration` and `pytest tests/unit -m postgres`, with PostgreSQL and SeaweedFS |
| `proxy` | The Lua specs | The Hurl files, with OpenResty, Valkey and a mock PostgREST |
| `console` | Vitest | The browser tests, with PostgreSQL, SeaweedFS, Keycloak, PostgREST and the console |
| `chart`, `nu` | The Helm chart tests and the Nushell tests | None |

## Run the suites

```bash
devenv --profile repo tasks run repo:test:unit                      # every :unit task
devenv --profile integration tasks run proxy:test:int               # one suite
devenv --profile integration tasks run sync:test:int console:test:int
```

Run one suite at a time on one machine. The suites share `.devenv/test-state`, and two runs can pick the same port. CI runs each suite on its own runner.

devenv skips a task when none of its input files changed since the last green run, and a skipped task takes under a second. A failed run leaves no state, so the task runs again. A change in `sync/src` runs the sync suite and the console suite, because the console tests seed their data with the sync code. To force a run, change an input or delete `.devenv/profiles/integration/state/tasks.db`. Every input is a file or a glob over files, because devenv compares a folder by its metadata, and a fresh checkout changes every folder. `utils.mkInputs` throws for a folder, and `utils.mkGlobs` throws for a file with an unlisted extension.

## The test stack

Each `:int` task runs `devenv test` for a profile. devenv starts the processes, waits for their `ready` checks and setup tasks and runs the tests.

| Profile | Processes |
| --- | --- |
| `sync` | PostgreSQL in Podman (the image of `Containerfile.pg`), SeaweedFS |
| `proxy` | Four OpenResty instances with a mock PostgREST each, and Valkey. The proxies start one after another, because each `lx exec` rewrites `proxy/lux.lock`. |
| `console` | The `sync` processes, Keycloak (the test image of `Containerfile.kc`), PostgREST, the console with its gateway, and a worker for the `control` queue |

Every process asks devenv for its port with `ports.<name>.allocate`. A port keeps its base number when it's free and moves up when it's busy. The setup passes the real number to the tests in environment variables. The base numbers are in `nix/stack/`. PostgreSQL starts empty on every run. `devenv --profile sync tasks run s3:clean:data` empties the object store.

Install Podman, or install Docker and set `CONTAINER_RUNTIME=docker`. The first PostgreSQL build is slow, and the engine keeps the layers. The image installs the `ducklake` extension while it builds, and an old layer fails the DuckLake tests. Refresh it with `podman build --no-cache`. To add a test container, declare a process with `utils.mkProcess { command = utils.podmanRun { ... }; }`, as `nix/stack/postgres.nix` does.

## Sync tests

The suite runs with `pytest-xdist` on up to four workers. Each worker clones its own database from `test_template`. A setup that every worker runs takes a file lock (`one_worker_at_a_time` in `sync/tests/helpers/core.py`). A new test uses only its own schema, so it runs on any worker.

- **Unit tests** check one function or template with every input. The template tests are in `sync/tests/unit/test_templates_*.py`, one file for each family: `duckdb`, `routing`, `filter`, `serving`, `access`, `maintenance` and `roles`. All but `duckdb` run on the real test server. The `rbac` tests also use Hypothesis, with 300 examples (`RBAC_EXAMPLES` sets another number).
- **Console SQL tests** build the console from the real migrations and templates with the fixture `console_api`. They cover the functions, the views, the import and the migrations on older data. `proxy/tests/unit/fixtures/console_config.json` is the document that `console.config()` writes, and a pytest case and the Lua specs both check it.
- **Integration tests** (`sync/tests/integration/`) check behavior across units. `test_pipeline.py` runs the body of `run_sync` with its real steps, a fake source and the real PostgreSQL, S3 and DuckLake.

These tests don't cover the API schemas, the database roles or what a token may do. The end-to-end suite covers those.

## Browser and proxy tests

`devenv --profile console test` runs the `playwright-bdd` features in `console/ui/tests/features/`: 94 scenarios in about 30 seconds, after about 25 seconds for the processes. The header of `console/ui/playwright.config.ts` explains the projects and the tags. Set `PLAYWRIGHT_GREP` to run some scenarios.

The proxy unit specs run with `nu scripts/lux.nu test`. The Hurl files in `proxy/tests/integration/` run through `devenv --profile proxy test` against the four proxies.

## End-to-end and load tests

The k6 suites need the k3d cluster, so CI doesn't run them. Run `cluster k6 e2e` before you merge a change to a permission, a role, an access policy or a view. The headers of `k6/e2e.js` and `k6/load.js` describe the scenarios, the log events, the traffic and the gates.

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

<!-- nav:start -->
---

[Previous: Development](./index.md) | [Next: CI and releases](./ci.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
