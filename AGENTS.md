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

- Never commit or push without explicit approval. Stage, summarize, and wait.
- Use Conventional Commits. The types are `feat`, `fix`, `refactor`, `test`, `docs` and `chore`. Never use `ci` or `perf`. See [Git](docs/style.md#git).
- Fix a flaky test at its cause, and add a test that reproduces it. Never add a retry.
