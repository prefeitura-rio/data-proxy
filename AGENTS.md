# Working in this repository

Data-proxy serves BigQuery data through PostgreSQL, DuckLake and an OpenResty proxy. Read the [style page](docs/style.md) for the files that you change.

## Commands

Run the checks before you finish a change. Run them inside `devenv`.

```bash
devenv --profile repo tasks run repo:lint repo:test:unit
devenv --profile integration tasks run sync:test:int proxy:test:int console:test:int
devenv --profile nix tasks run nix:lint:closure nix:lint:cmd
```

- The first line is the lint and the tests that need no service. The second line starts services, so run only the components that you changed.
- Run one task with `devenv --profile <profile> tasks run <task>`. The [development page](docs/dev/index.md#run-the-checks) lists the checks.

### Without devenv

When `devenv` or Podman is missing, run the lint and the unit tests of each component that you changed.

```bash
cd sync && uv run --frozen ruff check src tests && uv run --frozen ruff format --check src tests
cd sync && uv run --frozen pytest tests/unit -m "not postgres"
cd console/ui && pnpm install --frozen-lockfile && pnpm test && pnpm lint && pnpm check
cd terraform && go build ./... && go vet ./... && go test ./...
```

- These commands are a subset of the `devenv` tasks. The Lua checks, the type checks of `sync/` and the `:int` suites need `devenv`.
- CI runs every check and the `:int` suites on a pull request that changes a path of the filter in `.github/workflows/ci.yaml`. That run is the proof for the checks that you can't run.

## Where the code is

| Path                                | Content                                          |
| ----------------------------------- | ------------------------------------------------ |
| `proxy/`                            | OpenResty and Lua, the request path              |
| `sync/`                             | Python, DBOS workflows, DuckLake sync            |
| `console/ui/`, `console/notebooks/` | SvelteKit console and the notebook site          |
| `helm/`                             | The chart, the job scripts and the SQL templates |
| `k6/`                               | Load and end-to-end tests                        |
| `nix/`, `*/devenv.nix`              | The devenv profiles, processes and tasks         |
| `scripts/`                          | Nushell commands for developers and CI           |
| `docs/`                             | The documentation                                |

## Style by file

| Files                                     | Section                                                                |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| `proxy/**`                                | [Lua](docs/style.md#lua)                                               |
| `sync/**/*.py`                            | [Python](docs/style.md#python)                                         |
| `scripts/*.nu`, `helm/files/scripts/*.nu` | [Nushell](docs/style.md#nushell)                                       |
| `**/*.nix`, `devenv.*`                    | [Nix](docs/style.md#nix)                                               |
| `**/*.sql`, `rbac.toml`                   | [SQL](docs/style.md#sql)                                               |
| `console/ui/**`                           | [TypeScript](docs/style.md#typescript), [Svelte](docs/style.md#svelte) |
| `console/notebooks/**`                    | [Notebooks](docs/style.md#notebooks)                                   |
| `docs/**`, `README.md`                    | [Documentation](docs/style.md#documentation)                           |
| Any test, any log line                    | [Tests](docs/style.md#tests), [Logs](docs/style.md#logs)               |

## Rules that matter most

Read the [style guide](STYLEGUIDE.md) for the list with IDs. These come first:

- Read the code first, and follow an existing pattern (GEN-01, GEN-02).
- Prefer deletion to a new abstraction. Keep one source of truth (GEN-03, GEN-06).
- Never put a user value into SQL text, and never log a secret (SQL-02, LOG-03).
- Put a test at the lowest level that proves the behavior (TEST-01).
- Name a devenv task `<component>:<verb>:<target>` (NIX-01).

## Workflow

- Never push to `main`. A change reaches `main` only through a reviewed pull request.
- In an interactive session, never commit or push without explicit approval. Stage, summarize and wait.
- An autonomous agent commits and pushes to its own work branch, and opens a pull request. The approval applies to the merge into `main`.
- Use Conventional Commits. The types are `feat`, `fix`, `refactor`, `test`, `docs` and `chore`. Never use `ci` or `perf`. See [Git](docs/style.md#git).
- Fix a flaky test at its cause, and add a test that reproduces it. Never add a retry.
