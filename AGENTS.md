# Working in this repository

Data-proxy serves BigQuery data through PostgreSQL, DuckLake and an OpenResty proxy. Read the [style guide](STYLEGUIDE.md) section for the files that you change.

## Commands

Run the checks before you finish a change. Run them inside `devenv`.

```bash
devenv --profile repo tasks run repo:lint repo:test:unit
devenv --profile int tasks run sync:test:int proxy:test:int console:test:int
devenv --profile nix tasks run nix:lint:closure nix:lint:cmd
```

- The first line is the lint and the tests that need no service. The second line starts services, so run only the components that you changed.
- Run one task with `devenv --profile <profile> tasks run <task>`. The [checks page](docs/development/checks.md) lists every task and its profile.

## Where the code is

| Path | Content |
| --- | --- |
| `proxy/` | OpenResty and Lua, the request path |
| `sync/` | Python, DBOS workflows, DuckLake sync |
| `console/ui/`, `console/notebooks/` | SvelteKit console and the notebook site |
| `helm/` | The chart, the job scripts and the SQL templates |
| `k6/` | Load and end-to-end tests |
| `nix/`, `*/devenv.nix` | The devenv profiles, processes and tasks |
| `scripts/` | Nushell commands for developers and CI |
| `docs/` | The documentation |

## Style by file

| Files | Section |
| --- | --- |
| `proxy/**` | [Lua and OpenResty](STYLEGUIDE.md#lua-and-openresty) |
| `sync/**/*.py` | [Python](STYLEGUIDE.md#python) |
| `scripts/*.nu`, `helm/files/scripts/*.nu` | [Nushell](STYLEGUIDE.md#nushell) |
| `**/*.nix`, `devenv.*` | [Nix](STYLEGUIDE.md#nix) |
| `**/*.sql`, `helm/files/config/rbac.toml` | [SQL](STYLEGUIDE.md#sql) and [Permissions](STYLEGUIDE.md#permissions) |
| `console/ui/**` | [TypeScript and Svelte](STYLEGUIDE.md#typescript-and-svelte) |
| `console/notebooks/**` | [Notebooks](STYLEGUIDE.md#notebooks) |
| `docs/**`, `README.md` | [Documentation](STYLEGUIDE.md#documentation) |
| Any test | [Tests](STYLEGUIDE.md#tests) |

## Rules that matter most

- Read the code first, and follow an existing pattern before you add a new one.
- Prefer deletion to a new abstraction. Keep one source of truth for each fact.
- Write no comment inside a function body in Lua and TypeScript. Name the idea.
- Never put a user value into SQL text. Use `Identifier()`, `Literal()` or a parameter.
- Never log a credential, a token, a connection string or a query value.
- Put a test at the lowest level that proves the behavior, with table-driven cases that have an `id`.
- Name a devenv task `<component>:<verb>:<target>`. devenv rejects other names.
- Write docs in short sentences of 25 words or fewer.

## Workflow

- Never commit or push without explicit approval. Stage, summarize, and wait.
- Use Conventional Commits. The types are `feat`, `fix`, `refactor`, `test`, `docs` and `chore`. Never use `ci` or `perf`. See [Git](STYLEGUIDE.md#git).
- Fix a flaky test at its cause, and add a test that reproduces it. Never add a retry.
