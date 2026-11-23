# Working in this repository

Data-proxy serves BigQuery data through PostgreSQL, DuckLake and an OpenResty proxy. Read the [style guide](STYLEGUIDE.md) for the files that you change.

## Commands

Run the checks before you finish a change. Run them inside `devenv`.

```bash
devenv --profile sync test
devenv --profile proxy test
devenv --profile console test
devenv --profile provider test
devenv --profile chart test
devenv --profile docs tasks run docs:lint
devenv --profile tools test
```

- Each `test` command runs the lint and format checks first, then the unit tests, then the integration tests. The integration tests start services, so run only the components that you changed.
- Run one task with `devenv --profile <profile> tasks run <task>`. The [development page](docs/dev/index.md#run-the-checks) lists the checks.

### Without devenv

When `devenv` or Podman is missing, run the lint and the unit tests of each component that you changed.

```bash
cd sync && uv run --frozen ruff check src tests && uv run --frozen ruff format --check src tests && uv run --frozen pyrefly check
cd sync && uv run --frozen pytest tests/unit -m "not postgres"
cd console/ui && pnpm install --frozen-lockfile && pnpm test && pnpm lint && pnpm format:check && pnpm check
cd provider && test -z "$(golangci-lint fmt --diff ./...)" && go build ./... && go vet ./... && go test ./...
```

- `pytest` reads its settings from the environment. `devenv` sets them in the `env` block of `sync/devenv.nix`. Without `devenv`, export the keys of that block with placeholder values. The `not postgres` tests connect to no service.
- `sync:lint:py` in `sync/devenv.nix` has more Python checks. They also run with `uv run --frozen`.
- The Lua checks, the SQL lint and the `:int` suites need `devenv`.
- CI runs every check and the `:int` suites on a pull request that changes a path of the filter in `.github/workflows/ci.yaml`. That run is the proof for the checks that you can't run.

## Where the code is

| Path                                | Content                                          |
| ----------------------------------- | ------------------------------------------------ |
| `proxy/`                            | OpenResty and Lua, the request path              |
| `sync/`                             | Python, DBOS workflows, DuckLake sync            |
| `console/ui/`, `console/notebooks/` | SvelteKit console and the notebook site          |
| `helm/`                             | The chart, the job scripts and the SQL templates |
| `k6/`                               | Load and end-to-end tests                        |
| `provider/`                         | Go, the Terraform provider of Data Proxy         |
| `nix/`, `*/devenv.nix`              | The devenv profiles, processes and tasks         |
| `scripts/`                          | Nushell commands for developers and CI           |
| `docs/`                             | The documentation                                |

## Style by file

| Files                                     | Section                                                                |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| `proxy/**`                                | [Lua](STYLEGUIDE.md#lua)                                               |
| `sync/**/*.py`                            | [Python](STYLEGUIDE.md#python)                                         |
| `scripts/*.nu`, `helm/files/scripts/*.nu` | [Nushell](STYLEGUIDE.md#nushell)                                       |
| `**/*.nix`, `devenv.*`                    | [Nix](STYLEGUIDE.md#nix)                                               |
| `**/*.sql`, `rbac.toml`                   | [SQL](STYLEGUIDE.md#sql)                                               |
| `provider/**`                             | [Go](STYLEGUIDE.md#go)                                                 |
| `console/ui/**`                           | [TypeScript](STYLEGUIDE.md#typescript), [Svelte](STYLEGUIDE.md#svelte) |
| `console/notebooks/**`                    | [Notebooks](STYLEGUIDE.md#notebooks)                                   |
| `docs/**`, `README.md`                    | [Documentation](STYLEGUIDE.md#documentation)                           |
| Any test, any log line                    | [Tests](STYLEGUIDE.md#tests), [Logs](STYLEGUIDE.md#logs)               |

## Engineering guidance

Read the [style guide](STYLEGUIDE.md) and apply the [shared principles](STYLEGUIDE.md#principles) before the language-specific guidance. Use judgment about clarity and tradeoffs rather than inventing a prescription for each case.

Preserve security, data integrity and compatibility. Verify the intended behavior through the component checks, and propose configuration corrections when a style diagnostic encourages worse code.

## Workflow

- Never push to `main`. A change reaches `main` only through a reviewed pull request.
- In an interactive session, never commit or push without explicit approval. Stage, summarize and wait.
- An autonomous agent commits and pushes to its own work branch, and opens a pull request. The approval applies to the merge into `main`.
- Use Conventional Commits. The types are `feat`, `fix`, `refactor`, `test`, `docs` and `chore`. Never use `ci` or `perf`. See [Git](STYLEGUIDE.md#git).
- Fix a flaky test at its cause, and add a test that reproduces it. Never add a retry.
