# Checks

These sections list the checks that run during development and before a release.

## Development checks

Run the static and focused checks with devenv tasks:

```bash
devenv --profile default tasks run lint:all test:all
```

E2E tests run against the local cluster or staging environment, not in CI.

`lint:all` runs every task in the table. Run one task with `devenv --profile <profile> tasks run <task>`.

| Task | Profile | What it checks |
| --- | --- | --- |
| `lint:sync` | `sync` | Python with `ruff`, `basedpyright`, `pyrefly`, `complexipy` and `vulture`. |
| `lint:sql` | `sync` | The SQL templates and the console migrations, with `sqlfluff` and `goose validate`. |
| `lint:proxy` | `proxy` | The Lua code, with StyLua, luacheck and the LuaLS type check. |
| `lint:console`, `lint:nb` | `console` | The console app and the notebooks, with `svelte-check`, ESLint and Prettier. |
| `lint:chart` | `chart` | The Helm chart, with `helm lint --strict`. |
| `lint:ci` | `checks` | The GitHub workflows, with `actionlint`. |
| `lint:nu` | `checks` | The Nushell scripts, with `nu-lint`. |
| `lint:toml` | `checks` | The role-based access control (RBAC) rules file `rbac.toml`, with `taplo`. |
| `lint:containers` | `checks` | The container files, with `hadolint`. |
| `lint:diagrams` | `checks` | That each rendered diagram in `docs/diagrams/` matches its D2 source. |

`docs:build:diagrams` renders the D2 sources in `docs/diagrams/` to SVG files. `docs:build:all` runs it with `docs:build:nav` and `docs:build:roles`.

`docs:serve` runs `docs:build:all`, which renders the diagrams and writes the page footers, the role table and the sidebar. Then it serves the docs at `http://127.0.0.1:18200` and opens them in your browser, with `open` on macOS and `xdg-open` on Linux. A second run stops the old `mkdocs` server on the port. The task fails when another program uses the port. Set `DOCS_PORT` to use another port.

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

### Proxy rocks

The proxy uses [Lux](https://lux.lumen-labs.org/). `proxy/lux.toml` declares every Lua library and `proxy/lux.lock` pins them with source hashes.

**Layout**

- The Lua code lives in `proxy/src/proxy`.
- The unit specs are in `proxy/tests/unit`, one file for each module.
- The Hurl suite in `proxy/tests/integration/` covers the request path against a real nginx, Redis, and a mock PostgREST. `devenv --profile proxy test` starts and stops those processes.

**Types for the language server**

- LuaLS bundles the OpenResty types. `proxy/.luarc.json` reads them through `${3rd}` entries.
- The Penlight types come from the `penlight-types` input in `devenv.yaml` (`goldenstein64/pl-definitions`). devenv exports them as `PENLIGHT_TYPES`, and `proxy/.luarc.json` lists `${env:PENLIGHT_TYPES}` as a library.
- The LPeg types come from the `lpeg-types` input in `devenv.yaml` (`LuaCATS/lpeg`). devenv exports them as `LPEG_TYPES`, and `proxy/.luarc.json` lists `${env:LPEG_TYPES}` as a library.
- The Penlight definition files have no module name, so cast each Penlight `require` to its module class (`pl.ClassModule`, `pl.ListClass`, `pl.tablex`, `pl.stringx`). Without the cast, LuaLS types the module from the rock source, which has no annotations. Hover then shows `{ properties: unknown }` for `pl.class`.
- A `List(items)` built from a plain array has no item type for the language server. Annotate it if you need the type.

**C rocks**

- The `luafilesystem` rock that Penlight needs is a C module. It builds in the same image stage as `lpeg`.
- The `lpeg` rock is a C module too. The image build stage installs a compiler and copies the built library to `/opt/data-proxy/lib`.

**Lint**

- `lint:proxy` runs luacheck (through `lx lint`) and the LuaLS type check with every rule that works for this code.
- Luacheck uses the `ngx_lua` globals, a 120-character line limit, and a limit of 10 branches per function.
- LuaLS has every diagnostic group on, down to the hint level. Four checks are off:
  - `codestyle-check`, because stylua formats the code
  - `spell-check`, because it flags names such as `PostgREST`
  - `name-style-check` and `no-unknown`, because they reject conventions that the code uses on purpose

**Tools**

- Lux installs `busted` for `lx test`, and `stylua` and `luacheck` for `lx fmt` and `lx lint`. Nix therefore provides only LuaJIT, the language server for editors, and `lx`.
- `Containerfile.proxy` runs `lx build --only-deps` against the same lockfile. The image gets the runtime rocks and no test rocks.

```bash
devenv --profile proxy tasks run proxy:rocks
```

The task installs the rocks into the ignored `proxy/.lux/` tree and fails when `lux.lock` changes. Change a version with `lx add` or `lx update` in `proxy/`, then commit the lockfile. Add a test library with `lx add --test`. `lint:proxy` runs `lx fmt`, `lx lint`, and the LuaLS type check, and fails when formatting changed a file. OpenResty bundles `resty.redis`, `resty.string`, `resty.lock`, and `cjson`, so only `lua-resty-http` is a runtime rock. Run the checks with `devenv --profile proxy tasks run lint:proxy test:proxy`.

### Nushell tests

`helm/files/` has three folders. `config/` contains the settings and `rbac.toml`, `scripts/` contains the Nushell jobs and their tests, and `templates/` contains the SQL templates.

The Helm suite tests pure helpers from `helm/files/scripts/lib.nu` without Kubernetes,
PostgreSQL, rclone, or network access:

```bash
devenv --profile checks tasks run test:nu
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

<!-- nav:start -->
---

[Previous: Tests](tests.md) | [Next: Frontend](frontend.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
