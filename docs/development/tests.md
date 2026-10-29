# Tests

Run the unit tests that need no services with `devenv --profile default tasks run test:all`. The command runs these tasks:

- `test:proxy`: the proxy specs.
- `test:chart`: the chart tests.
- `test:sync`: the sync unit tests.
- `test:console`: the unit tests of the console.
- `test:nu`: the Nushell tests.

Run the three integration suites with separate commands. Each command starts only the processes that its suite needs, runs the tests, and stops the processes:

```bash
devenv --profile sync test    # PostgreSQL, SeaweedFS, then pytest
devenv --profile proxy test   # OpenResty with a mock PostgREST, Valkey, then the Hurl files
devenv --profile console test # PostgreSQL, SeaweedFS, Keycloak, PostgREST, the console, then the browser tests
```

## Run only what changed

Each of the three suites has a task in the `checks` profile. The task runs `devenv test` for the profile, and it lists the files that the suite reads:

```bash
devenv --profile checks tasks run itest:proxy  # the proxy suite
devenv --profile checks tasks run itest:sync   # the sync suite
devenv --profile checks tasks run e2e:console  # the console suite
```

devenv skips a task when none of its input files changed since the last green run, and the run takes under a second. A failed run leaves no state, so the task runs again. The same rule applies to the lint and unit tasks, so a change in `console/` doesn't run the proxy suite, and a change in `proxy/` doesn't run the console browser tests. A change in `sync/tests/unit` runs the sync suite only, and a change in `sync/src` runs the sync suite and the console suite, because the console tests seed their data with the sync code.

The inputs follow two rules. devenv compares a file by its content, so a fresh checkout doesn't run a task again. It compares a folder by its metadata, and every folder is new after a checkout. Every input is therefore a file, or a glob that ends in a file name or an extension, such as `console/ui/src/**/*.ts`. `utils.inputsOf` throws at evaluation for a folder, a missing file or a glob over a missing folder. `utils.filesOf` lists the extensions of a folder and throws when the folder has a file with another extension, so a new kind of file can't stay outside the inputs.

To force a run, change an input, delete `.devenv/profiles/checks/state/tasks.db`, or run `devenv --profile <name> test`. CI keeps the state of each group of jobs in the cache of GitHub Actions. A run on `main` saves it after a green job, and a pull request reads the state of `main` and doesn't save it. The input `force_full` of the workflow starts a run without the saved state.

The `sync` profile starts these processes:

- PostgreSQL, built from `Containerfile.postgres` and run on the host network. Install Podman first, or install Docker and set `CONTAINER_RUNTIME=docker`. The build needs network for the base image. The engine keeps the layer cache, so the first run is slow and later runs only check the layers. The image is `localhost/data-proxy-test-postgres:dev`.
- SeaweedFS for S3.

The `sync` suite tests the sync code and the SQL templates.

A unit test checks one function, class, method or template with every input and edge case. `sync/tests/unit/test_templates.py` contains all the template tests, as one class for each engine:

- The PostgreSQL class runs the templates on the real test server.
- The DuckDB class runs them on a real DuckDB with a file catalog.
- The BigQuery class uses a mock.

The `rbac` tests in that file include Hypothesis tests. They generate random rules files and compare the answers of `rbac.can()` and `rbac.mine()` with a reference that the test computes in Python. A property runs 300 examples by default, and `RBAC_EXAMPLES` sets another number.

Every template that has some logic has a test that renders it, runs it, and checks the result.

The console SQL has its own tests, on the real server and in the test transaction. The fixture `console_api` (`sync/tests/fixtures/console.py`) builds the console from the real migrations and the real templates, over the DBOS tables and the rules of `rbac.toml`. The tests in `test_console_api.py`, `test_console_config.py`, `test_console_templates.py` and `test_migrations.py` cover the functions that the console calls, the views and their scope by schema, the import and its preview, the history, and the migrations on data of an older shape.

The file `proxy/tests/unit/fixtures/console_config.json` is the document that `console.config()` writes for fixed rows. A pytest case checks that the database still writes it, and the Lua specs check that the proxy takes it. A change to one side breaks the other side.

`sync/tests/integration/test_pipeline.py` runs the body of `run_sync` with its real steps, a fake source, and the PostgreSQL, S3 and DuckLake of the test stack. It reaches no DBOS runtime, Kubernetes, Redis or PostgREST.

An integration test (`sync/tests/integration/`) checks a behavior that crosses units, such as the catalog, the state and S3.

The suite doesn't test the API schemas, the database roles, or what a token may do. The end-to-end suite tests those ([Roles](../security/roles.md#how-the-rules-are-tested) lists what it covers).

The `proxy` profile starts OpenResty with a mock PostgREST and Valkey. It needs no container engine.

The `console` profile starts these parts:

- the services of the `sync` profile
- Keycloak and PostgREST
- the console with its gateway
- a worker for the `control` queue
- the notebook site

It runs the browser tests of the console. [Frontend](frontend.md) describes it. A full run takes about one minute.

Every process asks devenv for its ports with `ports.<name>.allocate`. A port keeps its base number when it's free and moves up when it's busy. Two runs therefore never clash, and they can run at the same time. The setup passes the real number to the tests in environment variables.

| Suite | Base ports |
| --- | --- |
| `sync` | 15432 (PostgreSQL), 18333, 19333, 18082 and 18888 (SeaweedFS) |
| `console` | 15432 (PostgreSQL), 18333, 19333, 18082 and 18888 (SeaweedFS), 18180 (Keycloak), 13000 (PostgREST), 13001 (gateway), 13002 (notebooks), 13004 (console) |
| `proxy` | 18080, 18090, 18100 and 18110 (the four proxies), 18081, 18091, 18101 and 18111 (mock PostgREST), 18083, 18093, 18103 and 18113 (admin), 18399 (the Redis that the Redis-down proxy uses), 18379 (Valkey) |

The four proxies start one after another, because each `lx exec` rewrites `proxy/lux.lock` and two at the same time can read a half-written file.

The `default` profile runs the three suites in one `devenv test`.

devenv runs the task `sync:setup` (`scripts/db.nu`) before it starts pytest. If the task fails, the run stops and shows the error of the task.

The PostgreSQL image installs the `ducklake` and `postgres` extensions while it builds. A cached build layer keeps the version that it first downloaded. The sync worker writes DuckLake 1.0 catalogs, and `ducklake_catalog` in `sync/tests/fixtures/db.py` writes the test catalog with the same DuckDB. An image with an older `ducklake` extension fails the DuckLake tests. Rebuild with `podman build --no-cache` to refresh the extension.

On the GitHub `ubuntu-latest` runner (image `20261004.327.1`), Podman is 4.9.3. A cold build of the test image takes about 35 s. A cold `devenv --profile sync test` run takes about 60 s. CI therefore keeps no layer cache.

PostgreSQL starts empty on every run. The other services keep their files in `.devenv/test-state` for `devenv test` and in `.devenv/state` for `devenv up`. To start the object store empty, run `devenv --profile sync tasks run seaweedfs:clean`. The task deletes the SeaweedFS data folder and creates it again.

To add another test container, declare `processes.<name> = utils.mkProcess { command = utils.podmanRun { ... }; } // { ready = ...; }`, as `nix/processes/postgres.nix` does. Build an image in a task that runs `before` the process, and set `ready` to a command that runs in the container. For a one-shot step after the container is ready, declare a task with `after = [ (utils.processTask "<name>") ]` and `wantedBy` the same process. `podmanRun` uses the host Podman.

## Coverage

Each suite prints the lines that no test runs. There is no minimum percentage. Read the report to find a branch of your own code that no test reaches.

| Suite | Command |
| --- | --- |
| `sync` | `PYTEST_ADDOPTS='--cov=sync --cov-branch --cov-report=term-missing:skip-covered' devenv --profile sync test` |
| Console | `pnpm --dir console/ui coverage` |
| Proxy | `nu scripts/lux.nu coverage` |

The proxy report covers the modules that the unit specs load. The Hurl files cover `handler.lua` and `main.lua`, and the lines that call `resty.http` and `resty.redis`.

<!-- nav:start -->
---

[Previous: Setup](setup.md) | [Next: Checks](checks.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
