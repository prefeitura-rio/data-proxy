# Checks

These sections list the checks that run during development and before a release.

## Development checks

Run the static and focused checks with devenv tasks:

```bash
devenv --profile default tasks run repo:lint repo:test:unit
```

E2E tests run against the local cluster or staging environment, not in CI.

`repo:lint` runs every task in the table that the active profile has. CI uses the `repo` profile, so it doesn't run `docs:lint:diag`. Run one task with `devenv --profile <profile> tasks run <task>`.

| Task | Profile | What it checks |
| --- | --- | --- |
| `sync:lint:py` | `sync-unit` | Python with `ruff`, `pyrefly`, `complexipy` and `vulture`. |
| `sync:lint:sql` | `sync-unit` | The SQL templates and the console migrations, with `sqlfluff` and `goose validate`. |
| `proxy:lint:lua` | `proxy-unit` | The Lua code, with StyLua, luacheck and the LuaLS type check. |
| `console:lint:ui`, `nb:lint:ts` | `console-unit` | The console app and the notebooks, with `svelte-check`, ESLint and Prettier. |
| `chart:lint:helm` | `chart` | The Helm chart, with `helm lint --strict`. |
| `ci:lint:wf` | `repo` | The GitHub workflows, with `actionlint`. |
| `nu:lint:scripts` | `repo` | The Nushell scripts, with `nu-lint`. |
| `chart:lint:toml` | `repo` | The role-based access control (RBAC) rules file `rbac.toml`, with `taplo`. |
| `ctr:lint:files` | `repo` | The container files, with `hadolint`. |
| `nix:lint:closure` | `nix` | That the closure of each devenv profile isn't larger than its limit in `nix/profiles/nix.nix`. It fails with the five largest paths of a profile over its limit. |
| `nix:lint:cmd` | `nix` | That each `devenv` command in the docs, the README, `AGENTS.md` and the style guide names a profile and a task that exist. A task name can be a prefix, such as `repo`. |
| `docs:lint:diag` | `docs` | That each rendered diagram in `docs/diagrams/` matches its D2 source. |
| `docs:lint:code` | `repo` | That the code follows the rules that a pattern can check, such as no `assert` in the proxy, no `error make` in a script, no comment inside a function, and the quoting, parser, header and size rules of the SQL templates. It also checks the hashes of the console migrations. It also checks the last 50 commit subjects. |
| `docs:lint:style` | `repo` | That each rule in `docs/development/style/` has an ID, a level and a check, has at most 40 words, and has a unique ID, that a style page has at most 900 words, and that each cited ID exists. It also fails a sentence over 25 words, and a page over 1000 words unless the page is on a short list. |
| `docs:lint:links` | `repo` | That each link in `docs/`, `README.md` and `STYLEGUIDE.md` points to a file and, when it has one, a heading. A link to another site isn't checked. |

`docs:build:diag` renders the D2 sources in `docs/diagrams/` to SVG files. It runs in the `docs` profile, which adds `d2` and `mkdocs` to `repo`. In that profile `devenv tasks run docs:build` runs it with `docs:build:nav` and `docs:build:roles`, because devenv runs every task under a prefix.

`docs:serve:site` (profile `docs`) runs the `docs:build` tasks, which render the diagrams and writes the page footers, the role table and the sidebar. Then it serves the docs at `http://127.0.0.1:18200` and opens them in your browser, with `open` on macOS and `xdg-open` on Linux. A second run stops the old `mkdocs` server on the port. The task fails when another program uses the port. Set `DOCS_PORT` to use another port.

The sidebar follows `docs/.nav.json`. The task writes it to the generated file `mkdocs.nav.yml`, which `mkdocs.yml` loads, so run the task and not `mkdocs` alone. A link to a file outside `docs/` doesn't open in the served site.

### Git hooks

[prek](https://prek.j178.dev/) runs the hooks when you commit. Entering a devenv shell installs them. A hook checks only the staged files that match its filter. The hooks `eslint`, `prettier`, `lx-fmt`, `ruff` and `ruff-format` fix the files, so stage the result and commit again.

| Hook | Files | Work |
| --- | --- | --- |
| `eslint` | `console/ui` and `console/notebooks`: `.ts`, `.js`, `.mjs`, `.svelte` | Lints and fixes the code. |
| `prettier` | `console/ui` and `console/notebooks` | Formats the files. |
| `lx-fmt` | `proxy/**/*.lua` | Formats the Lua code with StyLua. |
| `ruff`, `ruff-format` | Python files | Lints, fixes and formats the code. |
| `ripsecrets` | every file | Fails when a file contains a secret. `.secretsignore` lists the exceptions. |
| `vale` | `docs/**/*.md`, `README.md`, `STYLEGUIDE.md`, `sync/src/sync/*.py`, `scripts/*.nu` | Checks the prose style. |

Vale runs only in this hook. CI doesn't run it. The hook runs `vale sync` first, which downloads the style packages and needs network the first time. Run one hook by hand with `prek run <hook> --files <file>` in a devenv shell. Run `vale <file>` for a quick check.

### Nushell tests

`helm/files/` has three folders. `config/` contains the settings and `rbac.toml`, `scripts/` contains the Nushell jobs and their tests, and `templates/` contains the SQL templates.

The Helm suite tests pure helpers from `helm/files/scripts/lib.nu` without Kubernetes,
PostgreSQL, rclone, or network access:

```bash
devenv --profile repo tasks run nu:test:unit
```

The task also runs the tests of `scripts/tests/lib.nu`, which cover the pure helpers of the local scripts.
`run.nu` takes the test files as arguments and finds every `test ...` command in them.

The Helm Job scripts and local cluster scripts remain separate codebases. Do
not import modules across them.

## Release checks

Run these before a release. The e2e suite and the checks of the other suites aren't enough: they don't test an upgrade or the limits of the load profile.

| Command | What it proves |
| --- | --- |
| `cluster k6 e2e` | The whole system works, including the lifecycle and resilience scenarios. |
| `cluster k6 load` | The latency gates pass, and Kubernetes Event-driven Autoscaling (KEDA) scales the proxy and PostgREST under load. |
| `cluster upgrade --from <previous chart version>` | The chart upgrades from the previous release: the migrations apply, the rules and the logins exist, and the access scenarios pass. It needs a published chart, so it can run only after the first release. |

The suites don't cover these points:

- traces
- the metrics that the sync workers send through OpenTelemetry (k3d has no collector)
- the in-flight limit of the proxy under load. The Hurl suite covers it only with a mock

See [Proxy rocks](rocks.md) for the Lua libraries of the proxy.

<!-- nav:start -->
---

[Previous: Sync tests](sync-tests.md) | [Next: Style rules](style/index.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
