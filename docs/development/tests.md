# Tests

A component has two tasks for its tests. `<component>:test:unit` runs the tests that need no service, and `<component>:test:int` runs the tests that need services. A namespace prefix runs every task under it in the active profile, so `devenv --profile repo tasks run sync:test` runs `sync:test:unit`. The sync tests that the [style rules](style/tests.md) call unit tests, but that run on the real PostgreSQL (marked `postgres`), belong to `sync:test:int`.

| Component | `:unit` (no service) | `:int` (services) |
| --- | --- | --- |
| `sync` | `pytest tests/unit -m "not postgres"` (464 tests) | `pytest tests/integration` and `pytest tests/unit -m postgres`, with PostgreSQL and SeaweedFS |
| `proxy` | the Lua specs | the Hurl files, with OpenResty, Valkey and a mock PostgREST |
| `console` | Vitest | the browser tests, with PostgreSQL, SeaweedFS, Keycloak, PostgREST and the console |
| `chart` | the Helm chart tests | none |
| `nu` | the Nushell tests | none |

For a quick run with no service, run `devenv --profile default tasks run repo:test:unit`. It runs every `:unit` task.

## Run only what changed

Each of the three `:int` tasks is in the `integration` profile, which extends only `base`. A CI job that runs one suite therefore doesn't fetch the tools of the other profiles, such as the Playwright browsers. The task runs `devenv test` for the profile, and it lists the files that the suite reads:

```bash
devenv --profile integration tasks run proxy:test:int    # the proxy suite
devenv --profile integration tasks run sync:test:int     # the sync suite
devenv --profile integration tasks run console:test:int  # the console suite
```

devenv skips a task when none of its input files changed since the last green run. A skipped task takes under a second. A failed run leaves no state, so the task runs again. The same rule applies to the lint and unit tasks. `repo:test:unit` doesn't run the `:int` tasks, because they start services and live in another profile.

Run one suite at a time on one machine. The suites share the folder `.devenv/test-state`, and devenv doesn't reserve a port that it allocates. Two runs that start together can pick the same port or delete the generated files of each other. CI runs each suite on its own runner, so it has no such conflict.

A change in `console/` doesn't run the proxy suite. A change in `proxy/` doesn't run the console browser tests. A change in `sync/tests/unit` runs the sync suite only. A change in `sync/src` runs the sync suite and the console suite, because the console tests seed their data with the sync code.

The inputs follow two rules. devenv compares a file by its content, so a fresh checkout doesn't run a task again. It compares a folder by its metadata, and every folder is new after a checkout. Every input is therefore a file, or a glob that ends in a file name or an extension, such as `console/ui/src/**/*.ts`. `utils.mkInputs` throws at evaluation for a folder, a missing file or a glob over a missing folder. `utils.mkGlobs` lists the extensions of a folder. It throws when the folder has a file with another extension, so a new kind of file can't stay outside the inputs.

To force a run, change an input, delete `.devenv/profiles/int/state/tasks.db`, or run `devenv --profile <name> test`. CI keeps the state of each group of jobs in the cache of GitHub Actions. A run on `main` saves it after a green job, and a pull request reads the state of `main` and doesn't save it. The input `force_full` of the workflow starts a run without the saved state.

Two more pages go into the detail. [Test stack](test-stack.md) describes the processes, the ports and how to add a test container. [Sync tests](sync-tests.md) describes the unit and integration tests of the sync suite.

## Coverage

Each suite prints the lines that no test runs. There is no minimum percentage. Read the report to find a branch of your own code that no test reaches.

| Suite | Command |
| --- | --- |
| `sync` | `PYTEST_ADDOPTS='--cov=sync --cov-branch --cov-report=term-missing:skip-covered' devenv --profile sync test`. It covers the `:int` tests. Add `--cov-append` and run `sync:test:unit` for the others. |
| Console | `pnpm --dir console/ui coverage` |
| Proxy | `nu scripts/lux.nu coverage` |

The proxy report covers the modules that the unit specs load. The Hurl files cover `handler.lua` and `main.lua`, and the lines that call `resty.http` and `resty.redis`.

<!-- nav:start -->
---

[Previous: Setup](setup.md) | [Next: Test stack](test-stack.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
