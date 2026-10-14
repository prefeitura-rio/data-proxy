# Style Guide

Rules for this repository. Follow an existing pattern before you add a new one.

## Principles

- Read the code before you write code.
- Prefer deletion to a new abstraction or configuration layer.
- Make small, reviewable changes. Don't bundle unrelated work.
- Fix the root cause, not the symptom.
- Keep one source of truth for each fact.
- Use the idiomatic tool for each ecosystem.

### Logs

These rules apply to all first-party code that logs from a deployment.

- Write human-readable lifecycle phrases: `<Component> <action> <state>: <relevant context>`.
- Use `debug` for probe detail, `info` for normal lifecycle changes, `warning` for retries or skipped optional work, and `error` only before a terminal error.
- Never log credentials, tokens, connection strings, request bodies, cache values, SQL values, or query strings.
- Don't use `print` for production logs. A documented machine-output protocol may write directly to stdout when callers parse it.
- Keep external tool output native. Don't parse or reformat output from `litestream`, `rclone`, `psql`, `pg_dump`, `duckdb`, or `kubectl`.

```text
S3 bucket is not ready; retrying: bucket=test-bucket error=connection refused
```

### State

- DBOS and PostgreSQL own orchestration state. Redis is a cache only. See [Architecture](docs/architecture.md).
- Keep DBOS workflows deterministic. Put I/O in steps.
- Don't re-implement DBOS checkpoints.
- See [Database Schema](docs/database.md) for roles, RLS, and privileged functions.

### Tests

Run the checks before you finish a change:

```bash
devenv --profile default tasks run lint:all test:all
devenv --profile sync test
devenv --profile proxy test
```

`test:all` runs the tests that need no services. `devenv --profile sync test` starts the services and runs every sync test. `devenv --profile proxy test` starts OpenResty and runs the Hurl proxy suite.

Put each test at the lowest level that can prove the behavior. Don't repeat a check at a higher level.

| Level       | Location                                               | Runs against                                                 | Covers                                                                  |
| ----------- | ------------------------------------------------------ | ------------------------------------------------------------ | ----------------------------------------------------------------------- |
| Unit        | `sync/engine/tests/unit/`, `proxy/tests/unit/`, `helm/tests/` | One process: mocks, local files, in-memory DuckDB            | Pure logic, DuckDB templates, workflow order, proxy, Helm manifests     |
| Integration | `sync/engine/tests/integration/`                              | devenv processes: PostgreSQL (built from `Containerfile.postgres`, via Podman), Silo, Valkey, PostgREST | PostgreSQL and Helm job SQL, DuckLake reads, routing, RLS, state, cache |
| Integration | `proxy/tests/integration/`                             | devenv processes: OpenResty, Valkey, a mock PostgREST        | Proxy request path: routing, retry, cache, filter pushdown              |
| E2E         | `k6/e2e.ts`, `k6/load.ts`                              | The deployed k3d cluster (`nu scripts/cluster.nu k6 e2e`)    | Sync, Keycloak and Istio auth, proxy, PostgREST headers, split sources  |

- Unit tests don't start containers or open network connections.
- Integration tests don't mock PostgreSQL. Mock only external services, such as BigQuery.
- E2E tests cover only what needs the deployed stack.
- Test modules hold only tests. Fixtures go in `sync/engine/tests/fixtures/`, test-double dataclasses in `sync/engine/tests/fixtures/types.py`, and builders in `sync/engine/tests/helpers.py`.
- Merge small test files that cover one module.
- Use table-driven cases with an `id` that names the scenario. Use Hypothesis only for invariants across generated inputs.
- Use `pytest-bdd` for integration behavior that crosses database, cache, or storage boundaries. Put feature files in `sync/engine/tests/integration/features/` and step definitions in `sync/engine/tests/integration/steps/`.
- Assert exact values. Don't use substring checks, except a regex assertion against generated text when an exact whole-document assertion would be brittle.
- Don't assert the full contents of a registry that grows.
- Don't test field storage, defaults, or "no exception was raised."

```python
@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        pytest.param(RangeSelection(partition_id="10", column="id", lower=10, upper=20),
                     '("id" >= 10 AND "id" < 20)', id="range"),
        pytest.param(RemainderSelection(column="id", start=0, end=100),
                     '("id" IS NULL OR "id" < 0 OR "id" >= 100)', id="remainder"),
    ],
)
def test_renders_exact_predicate(selection, expected):
    assert render(partition_condition(partition_for(selection))) == expected
```

## Python

### Code

- Use dataclasses for plain data. Use Pydantic models only for validation at boundaries.
- Model closed alternatives as tagged unions. Match every case; use `_` only for open sets.
- Don't prefix internal names with `_`. Use `_` only for a required but unused parameter.
- Keep `__init__.py` empty. Import from submodules.
- Inline a one-line helper that has one caller.
- Put a function in the module of its domain. Use `utils.py` only when no domain fits.
- Write multi-stage flows as a context dataclass with one method per stage, like `PlanningContext` in `planning.py`.
- Use explicit parameters and named functions. Avoid `**kwargs`, opaque lambdas, `Any`, and `object`.
- Run blocking calls through `asyncer.asyncify`.
- Treat JWT claims as identity only. Read authorization state from the database.
- Don't make outbound HTTP calls in request-time authorization.

```python
@dataclass
class PlanningContext:
    config: SyncConfig
    changed: dict[str, str] = field(default_factory=dict)

    async def detect(self) -> None: ...
    async def expand(self) -> None: ...
    def group(self) -> SyncWork: ...
```

### Logs

- Use the standard-library logger from `sync.log`. Don't use `print`, configure handlers in application modules, or create module-specific loggers.
- Configure the log level only through `Settings.LOG_LEVEL`. Don't set a log level in application modules.
- Preserve DBOS workflow, schema, and table context through the context values in `sync.log`.

```python
logger.info("DuckLake commit completed: published_tables=%d", len(published_tables))
```

## Lua and OpenResty

The proxy is OpenResty and Lua code in `proxy/src/proxy`. It runs in the pinned OpenResty image.

### Layout and dependencies

- Keep one named module for each concern: `parser`, `routing`, `request`, `cache`, `postgrest`, `response`, `log`, `config`.
- Write a module in this order: imports, local variables, local functions, classes, and the returned object (the module table, the functions on it, and `return`). Put a local variable that needs a local function right after that function. Declare the module table with the other local variables when nothing else needs it later.
- Name modules `proxy.<name>`. `nginx.conf` loads only `proxy.main`.
- Return the module table directly. Don't add re-export modules.
- Read the settings only through the table that `config.load` (`proxy/src/proxy/config.lua`) returns. Only `main` loads it, once at start-up in `main.init`, and passes it down as a parameter named `config`. Don't call `os.getenv` or read `ngx.var` for a setting in another module. Declare each setting, with its type, default and limits, in the fields of `proxy/src/proxy/schemas/definitions/config.lua`, and list its `PROXY_` variable in `helm/files/nginx.conf` with an `env` directive. `config` has no test file. The handler and main tests cover it.
- Define every named constant in `constants.lua`, in a group: `constants.redis.connect_timeout_ms`. Write group names and constant names in lower case. Describe each group with a small type in `constants.d.lua`, and compose the `Constants` type from them. Other modules define no constants.
- Define every Lua pattern that matches text in `regex.lua`, named after what it matches: `regex.parameter`, with a `---@field` line in `regex.d.lua`. Don't write such a pattern at the point of use. A single character stays in its `gsub` call, and a filter grammar stays in `parser/patterns.lua` as LPeg.
- Write grammars for query parameters with LPeg, in `parser/patterns.lua`. Don't split strings by hand.
- Use `ngx` APIs and pinned `lua-resty-*` modules.
- Declare every Lua library in `proxy/lux.toml`. Change versions only with `lx add` (`--test` for test libraries) or `lx update` in `proxy/`, and commit `proxy/lux.lock`.
- Don't list `busted` in `lux.toml`. Lux installs it, and `stylua` and `luacheck`, on its own.
- Add a rock only for a module that OpenResty lacks. OpenResty already has `resty.redis`, `resty.string`, `resty.lock`, `resty.sha256`, and `cjson`.
- Don't add LuaRocks steps to `Containerfile.proxy`. The image runs the locked `lx build --only-deps` and fails if `lux.lock` changes.
- Keep the rocks path in `Containerfile.proxy`, `helm/files/nginx.conf`, and `helm/tests/proxy_config_test.yaml` the same.
- Preserve the request-header allowlist and the cache-key isolation by identity, schema, representation, range, and DuckLake snapshot.
- Don't change the cache key or the cached value format without a migration plan. Old and new proxy versions share Redis during a rolling upgrade.

### Code

- Write a pure core and a thin shell. A pure function gets all its inputs as arguments, such as the configuration and the current time. It returns a new value.
- Only `main.lua`, `handler.lua`, the `Redis` class in `redis.lua`, `postgrest.send`, `log.write`, and `response.send` read or write `ngx.var`, `ngx.req`, `ngx.header`, `ngx.now`, `ngx.log`, or the network.
- Pure `ngx` string helpers, such as `ngx.escape_uri` and `ngx.decode_base64`, are allowed in the core.
- Keep module state only in `main.lua`, and the loaded schemas in the registry (`schemas/main.lua`). Other modules don't hold state.
- Build new tables. Don't change an argument.
- Don't use `assert` in code under `proxy/src`. Check the value, and raise an error with a message. Specs can use `assert`.
- Split a decision from its effect. Write `plan` to decide and `send` to act, as `response.plan` and `response.send` do.
- Use Penlight for data, strings and classes: `pl.List` for arrays (`List(items):filter(f):map(g)`), `pl.tablex` for maps and searches (`pairmap`, `find_if`, `foreach`, `update`), and `pl.stringx` for text (`strip`, `startswith`, `split`).
- Cast every Penlight `require` to its module class: `pl.ClassModule`, `pl.ListClass`, `pl.tablex` and `pl.stringx`, for example `local tablex = require("pl.tablex") --[[@as pl.tablex]]`. The definition files have no module name, so without the cast LuaLS types the module from the rock source, which has no annotations.
- Make every class with `pl.class`. A module table is a namespace, not a class. Declare the type of the class in the `.d.lua` file of its module, derive it from `pl.Class`, and give its constructor an `---@overload` that returns the class type. Then `X(...)` and `X()` are typed, and no `--[[@as ...]]` cast is needed where the class is made or called.
- Don't use the IO modules of Penlight (`pl.file`, `pl.dir`, `pl.path`, `pl.utils.readfile`) in a request. They block the nginx worker. Use them at start-up only, as `sync.load` and the schema registry do, and use `ngx.*` and `resty.*` in a request.
- Use a plain loop only for an index scan or ordered side effects.
- Use an object with methods only for state or I/O, such as the cache store. Use plain functions and tables for everything else.
- Return `value, err` for an error that the caller can handle. Raise an error for an input that the proxy must not run without, such as a missing file or setting.
- Use `pcall` only around an external boundary. Don't use it to hide a bug.
- Use guard clauses and lookup tables instead of long `if` chains.
- Give every number or repeated text a name in `constants.lua`. Don't put a bare number or a repeated string in code.
- Put a helper that works on plain values and knows nothing of its module in `utils.lua`, with its types in `utils.d.lua`. Keep a private helper that's coupled to its module in that module, as a local function: it builds, reads or checks a type, a constant or a variable of the module. A module also keeps its public functions and its module-level variables.
- Keep each function under the luacheck complexity limit of 10. Split a longer function into named steps.
- Inject external I/O at module boundaries, such as the `sender` and the `store`, so that unit tests can use fakes.

```lua
function postgrest.passed_headers(response_headers)
	return tablex.pairmap(function(_, name) return response_headers[name], name end, constants.headers.response)
end
```

### Types and documentation

- Describe every function with `---@param` and `---@return`. Describe every shared table with `---@class` and `---@field`.
- Put the types of a module in `<module>.d.lua`, next to `<module>.lua`. Start the file with `---@meta`, and write no other file comment. Put a type in the file of the module that creates it. Don't write types in `<module>.lua`, except the `---@class` of the module table itself, and the class of an object that the module implements with methods. LuaLS reports `inject-field` for a function that a file adds to a class that another file declares. Keep that class next to its `local` table, and declare no `---@field` for functions that the file defines.
- Don't use `table` or `any` if a named type exists.
- Define the fields of each schema in its own file, `proxy/src/proxy/schemas/definitions/<name>.lua`. The file returns only the fields: a table of field name to field type, built with `tableshape` (`require("tableshape").types`). `schemas/schema.lua` holds the `Schema` class: it makes the checker (`types.shape(fields, { open = true })`) and gives `validate`. Call one field type a field, not a shape, and keep the word `shape` for the `types.shape` call. `schemas/main.lua` is the registry singleton: when the module loads, at start-up, it registers each `<name>.lua` of `definitions/` as the schema `<name>`. A caller reads a schema as a field, `schemas.cached_entry`, with no call. Every checker is open, because every schema describes data from outside that can have other fields. To add a schema: make the file, and add `---@field <name> Schema<T>` to `SchemasModule` in the `.d.lua` file of the module that owns the data type `T`. Read data that the proxy doesn't create (files, Redis entries, tokens, environment variables) only through `schemas.<name>:validate(value)`, at the boundary where it enters. It gives the value, converted if the checker converts, or nil and a reason that names the field.
- Test the registry and every schema in `schemas_spec.lua`, table-driven: valid values, and invalid values with the field in the reason. For `config`, give a typed `Config` that has every field and compare it with the result. The language server can't compare a definition with its type, so a drift fails there: a new field of `Config` makes the typed value incomplete, and the test fails if the definition has a field that the type lacks. The specs of the modules that parse data (`sync`, `cache`, `request`) cover the rest.
- Write a one-line `---` description above every `---@class` and `---@alias`.
- Write a `---` summary line above every function and every constant. Use Simplified Technical English: short sentences, present tense, active voice. Write the word `if`, not `when`.
- Write comments as plain text. Don't use Markdown headings, tables, backticks, ASCII diagrams, or section banners.
- Start each file with plain `--` lines that say what the module does and why.
- Show an example of a function in a fenced `lua` block with `--> result`. This is the only place for a code fence.
- Don't write comments inside a function body. Use a name for the idea instead.
- Use `--[[@as Type]]` for a cast, and only at an external boundary.
- Don't use `---@diagnostic disable` in `proxy/src`. Fix the cause. A test file can disable `missing-fields`, because a case sets only the fields that it uses.
- Add the type definitions of a library that has none to `proxy/.types/`. Load LPeg types from `LPEG_TYPES` and Penlight types from `PENLIGHT_TYPES`.

### Logs

- Write logs only through `log.write`. Don't call `ngx.log` in other modules.
- Use the fixed messages in `constants.log.messages`. Add the context as `name=value` pairs.
- Don't write tokens, request bodies, cache values, or query strings to logs.

### Tests

- Write one test file for each module: `proxy/tests/unit/<module>_spec.lua`. Keep `proxy/tests/unit` flat.
- Write table-driven Busted specs. Use one `describe` for each function and `it(case.name)` for each case.
- Give each case a name that states the behavior, for example `retries a 503 on the retry service`.
- Keep fakes and fixtures in `proxy/tests/unit/helpers.lua` and `proxy/tests/unit/fixtures`. Make a fake follow the shape of the real API, for example the 32 raw bytes that `resty.sha256` gives.
- Test pure functions only. A function that has an effect, such as a request, a socket, or an nginx phase, belongs to `sync/engine/tests/integration/proxy/` or to k6, not to the unit specs.
- Compare a rewrite with the old code on random inputs before you delete the old code.

### Checks

- Run `devenv tasks run lint:proxy test:proxy` before review.
- `lint:proxy` runs luacheck (line length 120, complexity 10) with `lx lint`, and the LuaLS type check at the Hint level. The type check must report no problems.
- The git hook runs `lx fmt` (StyLua). Use tabs.
- Restore `proxy/lux.lock` with `git checkout -- proxy/lux.lock` after a sync that only reorders it.
- Start the editor inside the devenv shell, so that LuaLS can read `LPEG_TYPES`.

## Nushell

Nushell scripts run the Helm jobs and the local commands.

### Code

- Declare input and output types. Don't use `any` when a concrete type exists.
- Use full flag names: `open --raw`, `save --force`.
- Use `where` to filter, `match` to dispatch, and `get --optional` for optional fields.
- Start continuation lines with the pipe. Put one step on each line.
- Read optional variables with `$env.NAME? | default value`.
- Run SQL only through `execute-sql` from `helm/files/lib.nu`: one template, one context record. Quote values with `quote-pg`.
- Give each script one `main` command, and each CronJob its own script.
- Use pipelines for data transforms and `for` for ordered external side effects.
- Add input/output types and `@example` blocks to exported reusable helpers.
- `helm/files/` and `scripts/` are separate codebases. Don't import modules across them.
- Run `nu --ide-check` on changed files.

### Logs

- Use `std/log` directly. Don't use `print` for service output.

### Tests

Run the Helm Nushell suite after Helm script changes:

```bash
nu helm/files/tests/run.nu
```

```nu
use ./lib.nu [quote-pg execute-sql]

for schema in $schemas {
    execute-sql postgres/create_schema.sql {schema: (quote-pg $schema identifier)}
}
```

## SQL

Templates live in `sync/engine/src/engine/templates/<backend>/`. Helm job templates live in `helm/files/templates/postgres/`. See [SQL Templates](docs/sql-templates.md).

### Code

- Build SQL mappings with `Identifier()` and `Literal()`. Use parameters for runtime values.
- Never put user values into SQL text.
- Use `ON CONFLICT ... DO UPDATE` for upserts.
- Use `timestamptz`, `jsonb`, and `bigint GENERATED ALWAYS AS IDENTITY`.
- Use one transaction per logical operation, with the `atomic()` helper.
- Use `SECURITY DEFINER` only on purpose. Grant `EXECUTE` to the required role only.
- Mark read-only functions `STABLE`.
- Restart the configured PostgREST Deployments after serving definitions change. Don't use `NOTIFY pgrst`.
- Load and delete in sets, not row by row.

```python
mapping = {"table": Identifier(schema, name), "limit": Literal(n)}
```

### Backends

- PostgreSQL reads Parquet from object storage through `pg_duckdb`. Don't download it first.
- DuckDB writes one `COPY (...) TO ... (FORMAT PARQUET)` for each dump task.
- Put synchronization source behavior behind a `Source` adapter. Only the BigQuery adapter may use `bigquery_scan` or the BigQuery client.
- Give each `Source` adapter a Pydantic model for non-secret `source.settings`. Reject unknown, invalid, or empty settings when the registry creates the adapter.
- Keep `Source` construction side-effect free. Create external clients on first use and close them through the adapter lifecycle.
- Use a service account or Workload Identity. Don't put credentials in an image.

### Tests

- Put test SQL in `sync/engine/tests/templates/<backend>/`, with the same headers and names as `src/` templates. Inline only one short statement, such as `SELECT set_config(...)` or `SET ROLE`.

## Documentation

- Use Simplified Technical English: short sentences, active voice, one idea per sentence.
- Keep one topic in one document. Link to the owner; don't copy.
- Use generic examples unless a real value is required.
- Verify claims against source, Helm templates, tests, or a command you ran.

## Git

- Use Conventional Commits: `type(scope)!: imperative summary`, lowercase, no trailing period. The scope is optional.
- Types: `feat` for new user-visible capability; `fix` for a user-visible defect; `refactor` for internal restructuring; `test` for test-only work; `docs` for documentation-only work; `chore` for CI, tooling, dependencies, and maintenance.
- Don't use `perf` or `ci`: classify release-worthy performance work as `feat` or `fix`, and classify CI work as `chore`.
- Release levels: `feat!:` and `fix!:` bump major; `feat:` bumps minor; `fix:` bumps patch; all other types create no release.
- Use `!` only with `feat` or `fix` release commits. Explain the migration in the body. Don't use a `BREAKING CHANGE` footer.
- Make one logical change per commit. Squash fixups before merge.
- Never commit or push without explicit approval. Stage, summarize, and wait.

```text
feat(sync)!: require DBOS workflow recovery

Sync recovery now requires the DBOS worker and workflow state in PostgreSQL.

Migration:
- Deploy the DBOS worker before you enable scheduled syncs.
- Verify the DBOS system database is reachable.
```
