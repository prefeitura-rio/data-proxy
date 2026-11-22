# Development

This section helps you set up the repository, run the checks and find your way in the code. [Tests](tests.md) says how to run each suite. [CI](ci.md) describes the pipeline and the release. [Production deploys](promote.md) says how a release reaches production. [Terraform provider](../terraform.md#develop) covers the build, the tests and the releases of the provider. [Style](../style.md) has the rules for the code and the docs.

## Code map

```mermaid
{{#include ../diagrams/code-map.mmd}}
```

## Set up the local cluster

Install k3d, Podman, `kubectl`, Helm and the Google Cloud CLI. The Podman network needs DNS, and the shell that runs k3d needs the Docker-compatible socket variables of Podman.

```bash
devenv --profile default shell
gcloud auth application-default login
cluster up
```

`cluster up` stops before Helmfile when the application-default credentials are missing. It writes the credentials of the cluster to the ignored `k3d/.kubeconfig` and doesn't change your kubeconfig. It installs KEDA, k6, Istio, SeaweedFS, Keycloak, PostgreSQL with `pg_duckdb`, Valkey, PostgREST and Data Proxy.

| Command | Work |
| --- | --- |
| `cluster sync` | Applies the current local image tag. |
| `cluster sync --build` | Builds and pushes new local images, then applies them. |
| `cluster down` | Stops the cluster. Run `cluster down` and `cluster up` again when the Helm install fails. |
| `cluster` | Lists every command. |

`sync/` is one flat Python project. Its `pyproject.toml` has the settings of `ruff`, `pyrefly`, `complexipy`, `vulture` and `pytest`. `uv sync` prepares `sync/.venv`.

## How devenv sets up the repository

The root `devenv.yaml` imports `proxy/`, `sync/`, `console/`, `helm/`, `docs/` and `provider/`. Each folder has a `devenv.nix` with the profiles of its component. Run a profile with `devenv --profile <name> <command>`, where the command is `shell`, `up`, `test` or `tasks run <task>`.

| Profile | Use |
| --- | --- |
| `default` | Everything that a developer needs: the three stacks, the docs and the cluster scripts. |
| `sync`, `proxy`, `console`, `provider` | One component: its lint tasks, its unit task and its services, in the `devenv.nix` of its folder. `devenv --profile <component> test` runs every hook in parallel, then the unit tests, then the integration tests with the services, and stops them. `devenv up` starts the services. |
| `chart` | Helm, `cog` and the `ci` script. |
| `tools` | Repository checks and setup regression tests. `tools:lint` runs workflow, Nushell, container and source checks. `devenv --profile tools test` also runs the setup tests. |
| `base`, `python` | The parts that the other profiles share, in `nix/default.nix` and `nix/profiles/python.nix`. |

A profile fetches every package that its tasks and processes name, because devenv puts every task and process in the shell. Keep each profile small:

- Run the lint tasks by prefix, such as `devenv --profile sync tasks run sync:lint`. devenv runs only those tasks and what they need, and starts no service.
- The editor tools (language servers, `delve`) are off in every profile. The profile `default` turns them on for developers.
- Use `stdenvNoCC`. Only `proxy` keeps the compiler, because Lux builds C rocks.
- Select the output that the shell needs, such as `lib.getBin`, and not the whole package.

The CI pushes the paths that a public cache doesn't have to the cache `data-proxy` on Cachix. On your machine, run `devenv gc` to delete old environments. Set `auto-optimise-store = true` in `nix.conf`, or run `nix store optimise`, to link identical files. The services of the test stack are in `nix/services/`, the helpers in `nix/utils/` and the ports, addresses and test logins in `nix/data.nix`.

Every check is a task, and every task has a git hook made with `utils.mkHook`. The hook runs the task. At commit time, prek runs the hooks of the files that you changed. In `devenv test`, devenv runs `prek run -a` before `enterTest`, so every hook runs at the same time and `enterTest` starts only when all of them pass. `enterTest` runs the unit tests and then the integration tests as plain commands. A check never changes a file, so a failed hook tells you what to fix, and you run the formatter by hand. A task that follows the service setup would make devenv start the service twice, so no test is a task.

A source check skips its work when its inputs match the last green run. Artifact builds run each time, because task-state caches omit generated files. The code check also runs each time because it reads Git history. Cocogitto validates recent commit syntax, and repository rules check commit types and summary capitalization. To force cached checks, use `devenv --profile <profile> tasks run --refresh-task-cache <task>`.

## Run the checks

```bash
devenv --profile sync test
devenv --profile proxy test
devenv --profile console test
devenv --profile provider test
devenv --profile chart test
devenv --profile docs tasks run docs:lint
devenv --profile tools test
```

| Task | Checks |
| --- | --- |
| `sync:lint:py`, `sync:lint:sql`, `sync:lint:fmt` | Python and SQL: `ruff`, `pyrefly`, `complexipy`, `vulture`, `sqlfluff`, `goose validate` and `ruff format --check`. |
| `proxy:lint:lua`, `proxy:lint:fmt` | Lua: luacheck, the LuaLS type check and `stylua --check`. |
| `console:lint:ui`, `console:lint:fmt`, `nb:lint:ts`, `nb:lint:fmt` | The app and the notebooks: `svelte-check --tsgo`, Oxlint, ESLint for `.svelte` files and `oxfmt --check`. |
| `chart:lint:helm`, `chart:lint:toml` | The chart and `rbac.toml`. |
| `tools:lint:wf`, `tools:lint:ctr`, `tools:lint:nu` | Workflows, container files and Nushell scripts. |
| `tf:lint:go`, `tf:lint:fmt` | The provider: `golangci-lint` and the format check of `golangci-lint fmt --diff`. |
| `tools:lint:code` | Source rules and recent commits. Cocogitto checks Conventional Commit syntax, while repository policies enforce the types in `cog.toml` and lowercase summary starts. |
| `docs:lint:prose`, `docs:lint:book` | Vale checks the prose. mdBook builds the bundle, and Lychee checks its file and heading links. A missing chapter file fails the build. |

The git hooks run when you commit. They only check and never change a file, so fix the file by hand and commit again. `ripsecrets` blocks a secret. The docs task checks Markdown with Vale, while the direct Vale hook checks code comments. CI checks Markdown as `docs:lint:prose`. Run `vale <file>` in the docs shell to check a file.

Before a release, run the checks that CI can't run, with a valid Google login. `cluster k6 e2e` tests the whole system. `cluster k6 load` tests the latency gates and KEDA. `cluster upgrade --from <previous chart version>` tests the upgrade path.

## Run the console locally

```bash
devenv --profile console up
```

This also starts the notebook server, which a test run skips.

Add `-d` to run in the background, wait with `devenv processes wait` and stop with `devenv down`. Open `http://console.localhost:13001/console`, or run `devenv --profile console tasks run console:open:browser`. Sign in as `admin` (role `dp_admin`, schema `test`) with the password `admin`. The tests also use six users with the password `web-test`:

- `web_admin`: role `dp_admin`, every schema.
- `web_console`: role `dp_console`.
- `web_api`: role `dp_api`.
- `web_policy`: role `dp_policy`.
- `web_other`: role `dp_console`, schema `other`.
- `web_root`: role `dp_admin`, every schema.

No sync worker runs, so a sync run that you start stays `ENQUEUED`. A worker for the queues `control` and `schedules` runs, so cancel, resume and the schedule actions work.

## Write the docs

`devenv --profile docs up docs` serves the book with mdBook. The page order and the sidebar come from `docs/SUMMARY.md`, which you edit by hand. No script generates it. The Mermaid scripts come from the `mdbook-mermaid` package, so the preview loads nothing from the internet. devenv prints the address. The port is 18200, or the next free port when 18200 is busy.

The sidebar starts with nested sections collapsed. Use the arrow beside a chapter to expand or collapse its section. The settings are in `[output.html.fold]` in `book.toml`.

### Move a page

Update `docs/SUMMARY.md` and the links to the page. Add a native redirect in `book.toml` when the old address needs to keep working. For example, if `old-usage.md` moves to `using.md`, add:

```toml
[output.html.redirect]
"/old-usage.html" = "using.html"
"/old/usage.html" = "../using.html"
```

These mappings are examples, not active redirects. Each key starts with `/` and names an old path inside the book. Each destination is relative to its old page. Never prefix a destination with `/`, `latest/` or a version number. This keeps the redirect inside the current publication, under both `latest/` and version folders. Add a mapping only for a real page move.

### Diagrams and exports

Each diagram has one `.mmd` source file in `docs/diagrams/`. Its owner page includes the file inside a `mermaid` fence through native mdBook inclusion. Keep the diagram name in a `%%` comment on the first line of its source.

The diagrams of [Data model](../model.md) come from the database. Run `devenv --profile docs tasks run docs:build:model`. The task starts the databases, writes two `.mmd` files for owned tables, then stops the databases. mdBook includes the diagrams in the model page. The application diagram covers our tables in the application database. The catalog diagram contains only `signer.keys`. The diagrams omit DBOS and DuckLake internal tables.

`docs/.tbls.yaml` contains two named sections, `application` and `catalog`. Each section is a tbls configuration for its database connection. The generator passes each section to tbls through standard input. It creates no temporary configuration files. Commit the generated files after a migration.

Run `devenv --profile docs tasks run docs:build:site` to build the complete bundle in `.book/html/`. mdBook writes HTML and Markdown, then Repomix packs the Markdown into `llms.txt`. The bundle also contains `SUMMARY.md`, the chapter Markdown and the assets. `repomix.config.json` controls the export. The preview serves the book without the Repomix export. Nothing of this goes into Git.

The book includes `rbac.toml` directly in [Roles](../security/roles.md). Edit `docs/SUMMARY.md` when you add a chapter. mdBook ignores Markdown pages that you omit from that file. Review the style-rule IDs and the documented devenv commands by hand.

## Database migrations and the local API

Run `migrate up`, `migrate status` or `migrate down` in the console shell with `PG_DATABASE_URL` set. Use `--database ducklake` and `DUCKLAKE_ADMIN_URL` for the catalog database. [Using the API](../using.md#try-it-locally) shows how to get a token and call the local API.

Goose reads application migrations from `migrations/console/` and catalog migrations from `migrations/ducklake/`. It records applied versions in `public.goose_db_version` in each database. Migrations define extensions, schemas, tables, views, functions, triggers and request-role grants. Configuration that depends on credentials, `rbac.toml`, console schemas or a sync run stays in the chart and templates.

Create a migration with `goose -dir migrations/console create <name> sql`. Write its Up and Down parts, including grants for any new console table, view or function. Never edit a migration that a shared database already applied. Add a migration instead, then run `nu scripts/rules.nu sums` to update the checksums. `sync:lint:sql` checks SQLFluff rules and `goose validate`.

Goose substitutes role names between `-- +goose ENVSUB ON` and `-- +goose ENVSUB OFF`. Quote each role name and keep `$$` function bodies outside that region, because substitution also changes dollar signs. The deployment order is request roles, DBOS migrations, Goose migrations, then rules and schema provisioning. See [Initialization](../operations/initialization.md).
