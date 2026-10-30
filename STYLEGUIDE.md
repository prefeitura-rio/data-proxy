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
- Keep external tool output native. Don't parse or reformat output from `rclone`, `psql`, `pg_dump`, `duckdb`, or `kubectl`.

```text
S3 bucket is not ready; retrying: bucket=test-bucket error=connection refused
```

### State

- DBOS and PostgreSQL own orchestration state. Redis is a cache only. See [Architecture](docs/architecture/index.md).
- Keep DBOS workflows deterministic. Put I/O in steps.
- Don't re-implement DBOS checkpoints.
- See [Database Schema](docs/database.md) for roles, RLS, and privileged functions.

### Tests

Run the checks before you finish a change:

```bash
devenv --profile default tasks run lint:all test:all
devenv --profile sync test
devenv --profile proxy test
devenv --profile console test
```

`test:all` runs the tests that need no services. `devenv --profile sync test` starts the services and runs every sync test. `devenv --profile proxy test` starts OpenResty and runs the Hurl proxy suite. `devenv --profile console test` starts the whole console stack and runs the browser tests. It takes about one minute.

Put each test at the lowest level that can prove the behavior. Don't repeat a check at a higher level.

A unit test checks one unit exhaustively: a function, a class, a method, or an SQL template. It covers every input, every branch, and the edge cases. An integration test checks a behavior. The scenario can cross units or services, such as a state commit, a catalog read, or a request through the proxy. The infrastructure doesn't decide the level. A template belongs to the unit level even when it runs on the real PostgreSQL server, because the test checks that template alone.

| Level       | Location                                                | Runs against                                                                                                    | Covers                                                                                                                    |
| ----------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Unit        | `sync/tests/unit/`, `proxy/tests/unit/`, `helm/tests/`  | Mocks, local files, in-memory DuckDB, and for the PostgreSQL templates the real test server (`postgres` marker) | Every behavior of a function, class, method or template: pure logic, SQL templates, workflow order, proxy, Helm manifests |
| Integration | `sync/tests/integration/`                               | devenv processes: PostgreSQL (built from `Containerfile.postgres`, via Podman), SeaweedFS                       | Scenarios across units: DuckLake catalog, state, S3, and flows that cross database, cache or storage                      |
| Integration | `proxy/tests/integration/`                              | devenv processes: OpenResty, Valkey, a mock PostgREST                                                           | Proxy request path: routing, retry, cache, filter pushdown                                                                |
| Unit        | `console/ui/src/**/*.test.ts`                           | Vitest, with no service and no `$app` or `$env` import                                                          | Every behavior of a module in `lib/` and `lib/server/`                                                                    |
| Integration | `console/ui/tests/`                                      | devenv processes: PostgreSQL, SeaweedFS, Keycloak, PostgREST, the console, a browser                            | The console as a user sees it: sign-in, tabs, forms, run control                                                         |
| E2E         | `k6/e2e.js`, `k6/scenarios/`, `k6/load.js`              | The deployed k3d cluster (`nu scripts/cluster.nu k6 e2e`)                                                       | Sync, Keycloak and Istio auth, proxy, PostgREST headers, split sources                                                    |

- A unit test calls no service except the engine of the template that it tests: the PostgreSQL test server or an in-memory DuckDB. Mark a test that needs the server with `postgres`. `test:sync` skips those tests.
- Integration tests don't mock PostgreSQL. Mock only external services, such as BigQuery.
- E2E tests cover only what needs the deployed stack.
- In `k6/`, put every helper in a module of `k6/lib/`, and let `k6/lib/index.js` re-export it. A scenario file contains only its steps, and `load.js` contains only its orchestration and its iteration functions. One check asserts one condition. Write log lines with `info` and `warn` from `k6/lib/log.js`, with an event name and `key=value` fields, and never with `console`. The k6 files are plain JavaScript with JSDoc types, and `tsc` checks them. All types are in `k6/lib/types.d.ts`. It declares the k6 modules and the shapes of the code. The shapes are global types, so a file uses `Step` or `TokenData` in a `@param` without an import. Write a new type there, and give every function parameter a `@param` type. Keep the file a script: a top-level `import` or `export` would turn it into a module, and the declarations of the k6 modules would fail. Use `import("k6/http").Response` in it. A `.d.ts` file never shares its name with a `.js` file of the same folder, because `tsc` would then skip the `.js` file. Type external data at its source: a type guard such as `isRecord` or `recordsOf` for JSON. Don't cast: read an unknown value with `String`, `Number` or a type guard. Put a blank line between two statements when one of them spans several lines.
- Test modules contain only tests. Fixtures go in `sync/tests/fixtures/`, test-double dataclasses in `sync/tests/fixtures/types.py`, and builders in `sync/tests/helpers.py`.
- Merge small test files that cover one module.
- Test your own code, and no code of a third party. Don't write a test of DBOS, PostgREST, Keycloak, Kubernetes, Redis, a cloud SDK or a browser engine. Stub the call at the edge of your code, and test what your code does with the answer.
- Use table-driven cases with an `id` that names the scenario. Use Hypothesis only for invariants across generated inputs.
- Use `pytest-bdd` for integration behavior that crosses database, cache, or storage boundaries. Put feature files in `sync/tests/integration/features/` and step definitions in `sync/tests/integration/steps/`.
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

- Type a password, a key or an address with a password as `SecretStr`, and call `get_secret_value()` only where the code connects. Give a credential no default.
- Use dataclasses for plain data. Use Pydantic models only for validation at boundaries.
- Model closed alternatives as tagged unions. Match every case. Use `_` only for open sets.
- Don't prefix internal names with `_`. Use `_` only for a required but unused parameter.
- Keep `__init__.py` to one docstring line, because `ruff` requires it. Import from submodules.
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

### Tooling

`devenv --profile default tasks run lint:sync` runs `ruff` with `select = ["ALL"]`, `pyrefly` (with `preset = "all"`), `complexipy` (a limit of 10 for each function) and `vulture`. Don't use `Any`. Use `cast` once, in `templates.py`, where it marks the text of a repository template as `LiteralString`. Don't use implicit string concatenation. Write one long string. `E501` is off.

## Lua and OpenResty

The proxy is OpenResty and Lua code in `proxy/src/proxy`. It runs in the pinned OpenResty image.

### Layout and dependencies

- Keep one named module for each concern: `cache`, `config`, `constants`, `handler`, `limiter`, `log`, `main`, `parser`, `postgrest`, `redis`, `regex`, `request`, `response`, `schemas`, `settings`, `sync` and `utils`.
- Write a module in this order: imports, local variables, local functions, classes, and the returned object (the module table, the functions on it, and `return`). Put a local variable that needs a local function right after that function. Declare the module table with the other local variables when nothing else needs it later.
- Name modules `proxy.<name>`. `nginx.conf` loads only `proxy.main`.
- Return the module table directly. Don't add re-export modules.
- Read the `PROXY_*` variables only through the table that `config.load` (`proxy/src/proxy/config.lua`) returns. Only `main` loads it, once at start-up in `main.init`, and passes it down as a parameter named `config`. Don't call `os.getenv` or read `ngx.var` for a setting in another module. Declare each setting, with its type, default and limits, in the fields of `proxy/src/proxy/schemas/definitions/config.lua`, and list its `PROXY_` variable in `helm/files/config/nginx.conf` with an `env` directive. The module `settings` is another thing: it keeps the `sync.json` table that every worker shares, and the console replaces it.
- Define every named constant in `constants.lua`, in a group: `constants.redis.connect_timeout_ms`. Write group names and constant names in lower case. Describe each group with a small type in `constants.d.lua`, and compose the `Constants` type from them. Other modules define no constants.
- Define every Lua pattern that matches text in `regex.lua`, named after what it matches: `regex.parameter`, with a `---@field` line in `regex.d.lua`. Don't write such a pattern at the point of use. A single character stays in its `gsub` call, and a filter grammar stays in `parser/patterns.lua` as LPeg.
- Write grammars for query parameters with LPeg, in `parser/patterns.lua`. Don't split strings by hand.
- Use `ngx` APIs and pinned `lua-resty-*` modules.
- Declare every Lua library in `proxy/lux.toml`. Change versions only with `lx add` (`--test` for test libraries) or `lx update` in `proxy/`, and commit `proxy/lux.lock`.
- Don't list `busted` in `lux.toml`. Lux installs it, and `stylua` and `luacheck`, on its own.
- Add a rock only for a module that OpenResty lacks. OpenResty already has `resty.redis`, `resty.string`, `resty.lock`, `resty.sha256`, and `cjson`.
- Don't add LuaRocks steps to `Containerfile.proxy`. The image runs the locked `lx build --only-deps` and fails if `lux.lock` changes.
- Keep the rocks path in `Containerfile.proxy`, `helm/files/config/nginx.conf`, and `helm/tests/proxy_config_test.yaml` the same.
- Preserve the request-header allowlist and the cache-key isolation by identity, schema, representation, range, and DuckLake snapshot.
- Don't change the cache key or the cached value format without a migration plan. Old and new proxy versions share Redis during a rolling upgrade.

### Code

- Write a pure core and a thin shell. A pure function gets all its inputs as arguments, such as the configuration and the current time. It returns a new value.
- Only `main.lua`, `handler.lua`, the `Redis` class in `redis.lua`, `postgrest.send`, `log.write`, and `response.send` read or write `ngx.var`, `ngx.req`, `ngx.header`, `ngx.now`, `ngx.log`, or the network.
- Pure `ngx` string helpers, such as `ngx.escape_uri` and `ngx.decode_base64`, are allowed in the core.
- Keep module state only in `main.lua`, and the loaded schemas in the registry (`schemas/main.lua`). Other modules don't keep state.
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
- Define the fields of each schema in its own file, `proxy/src/proxy/schemas/definitions/<name>.lua`. The file returns only the fields: a table of field name to field type, built with `tableshape` (`require("tableshape").types`). `schemas/schema.lua` contains the `Schema` class: it makes the checker (`types.shape(fields, { open = true })`) and gives `validate`. Call one field type a field, not a shape, and keep the word `shape` for the `types.shape` call. `schemas/main.lua` is the registry singleton: when the module loads, at start-up, it registers each `<name>.lua` of `definitions/` as the schema `<name>`. A caller reads a schema as a field, `schemas.cached_entry`, with no call. Every checker is open, because every schema describes data from outside that can have other fields. To add a schema: make the file, and add `---@field <name> Schema<T>` to `SchemasModule` in the `.d.lua` file of the module that owns the data type `T`. Read data that the proxy doesn't create (files, Redis entries, tokens, environment variables) only through `schemas.<name>:validate(value)`, at the boundary where it enters. It gives the value, converted if the checker converts, or nil and a reason that names the field.
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
- Use the fixed messages in `constants.log.messages`. Add the context as `name=value` pairs. Use `log.note` for an event that has no request, such as a timer or the Redis store.
- Don't write tokens, request bodies, cache values, or query strings to logs.

### Tests

- Write one test file for each module: `proxy/tests/unit/<module>_spec.lua`. Keep `proxy/tests/unit` flat. `config`, `constants`, `regex`, `main`, `handler` and `log` have no test file.
- Write table-driven Busted specs. Use one `describe` for each function and `it(case.name)` for each case.
- Give each case a name that states the behavior, for example `retries a 503 on the retry service`.
- Keep fakes and fixtures in `proxy/tests/unit/helpers.lua` and `proxy/tests/unit/fixtures`. Make a fake follow the shape of the real API, for example the 32 raw bytes that `resty.sha256` gives.
- Test pure functions only. A function that has an effect, such as a request, a socket, or an nginx phase, belongs to the Hurl suite in `proxy/tests/integration/` or to k6, not to the unit specs.
- Compare a rewrite with the old code on random inputs before you delete the old code.

### Checks

- Run `devenv --profile proxy tasks run lint:proxy test:proxy` before review.
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
- Run SQL only through `execute-sql` from `helm/files/scripts/lib.nu`: one template, one context record. Quote values with `quote-pg`.
- Give each script one `main` command, and each CronJob its own script.
- Use pipelines for data transforms and `for` for ordered external side effects.
- Add input/output types and `@example` blocks to exported reusable helpers.
- `helm/files/scripts/` and `scripts/` are separate codebases. Don't import modules across them.
- Run `nu --ide-check` on changed files.
- Stop a script with `fail $message` from `lib.nu`. It logs the message and raises an error that has a label on the message in the caller. Don't call `error make` in a script.
- Read and write files only through the helpers of `lib.nu` (`read-text`, `read-data`, `write-text`, `parse-json`, `parse-toml`, `parse-yaml`, `make-dir`, `remove-path`). They wrap the builtin in `try` and name the file in the error. Wrap `cd` and `http get` in `try` at the call.
- Let an error of a builtin or an external command stop the script. Use `try` only when you add context that the error lacks, such as the table that failed, or when you clean up.
- Use `check-result` for the record that `complete` returns, and `wait-until` for a wait. Don't write the exit-code check or the polling loop again.
- Write an expression that three places repeat as a helper (`app-schema`, `role-literals`), and keep one table for a list that several commands read (`IMAGES` in `scripts/lib.nu`).
- `default $env.NAME` reads the variable even when the value isn't null, and it fails when the variable isn't set. Use `default {|| $env.NAME }` for a variable that a caller may not need.
- Use one command for one kind of quote (`quote-identifier`, `quote-literal`). Don't pass the kind as a string.
- `devenv --profile checks tasks run lint:nu` runs `nu-lint`, and it fails when `nu-lint` reports a finding, because `nu-lint` exits with 0 in that case. Each rule that `.nu-lint.toml` turns off has a comment with the reason.

### Logs

- Use `std/log` directly. Don't use `print` for service output. A command in `scripts/` may use `print` for machine output, such as a CI output line, or for a log stream that it shows to a person.

### Tests

Run the Nushell tests after a script change. `run.nu` finds every `test ...` command of the test files, so a new test needs no registration:

```bash
devenv --profile checks tasks run test:nu
```

```nu
use ./lib.nu [quote-pg execute-sql]

for schema in $schemas {
    execute-sql postgres/create_schema.sql {schema: (quote-pg $schema identifier)}
}
```

## Nix

Nix files build the test setup of the devenv profiles. The profile of a component lives in `<component>/devenv.nix`, and the root `devenv.yaml` imports it. `nix/profiles/` contains the profiles that belong to no component. `nix/processes/` contains one component for each file, with its processes and tasks, and `nix/utils.nix` contains the helpers.

### Code

- Format with `nixfmt`. Don't align by hand.
- Put every web address in quotes. Don't use a bare address.
- Don't use `rec`. Bind the shared names in a `let` block and use `inherit`.
- Don't use `with` at the top of a file or around code. Use `with pkgs; [ ... ]` only for a list of packages.
- Don't use lookup paths such as `<nixpkgs>`. The lock file of devenv pins every input.
- Name the arguments of a function in a set pattern, and give an optional argument a default with `?`.
- Use `inherit (set) name;` instead of `name = set.name;`.
- Write a path that Nix must track as a path literal, such as `../../Containerfile.postgres`. A string path breaks in the sandbox.
- Keep a value in one place. A login and an address live in `nix/data.nix`, and a port lives on its process.
- Use bash only for a script that's a list of plain commands, with no loop, condition or variable, such as `scripts.seed` or an `enterTest`. Write anything else in Nushell, or run a plain command. See the [Nushell](#nushell) section.
- Prefer a plain command with `env` and `cwd` to a generated wrapper script.
- Put a profile in the folder of the component that it tests, and list the folder under `imports:` in `devenv.yaml`. Put an input that only one component uses in the `devenv.yaml` of that folder.
- Declare the port of a process with `ports.<name>.allocate = <base>` and read it with `utils.port "<process>" "<name>"`. Don't write a port number in another place.
- Give every task and every script a `description`.
- Take `utils` from the module arguments. Don't import `nix/utils.nix` in a module.
- Make a service that stays up a `processes` entry. Make a command that runs and exits a `tasks` entry. Don't run a one-shot command as a process.
- Name a task `<component>:<step>`, such as `console:setup:seed`. Use `utils.mkTask` for one task and `utils.mkTaskChain` for steps in order. `mkProcess` and `mkTask` build only the command and the variables. Add the other options of the devenv process or task with `//`.
- A task that a process needs uses `before`. A task that needs a running service uses `after` and `wantedBy` with `utils.processTask "<name>"`.
- Name the task of a suite that starts services `test:int:<component>`. `test:all` skips these tasks.
- Use `execIfModified` on a build task, on a lint or unit task, and on a suite that starts services, and give it the input files with `utils.mkInputs`. List files and globs that end in an extension, never a folder or `dir/**`. Use `utils.mkGlobs` for the files of a folder.
- Build a file from variables with the `files` option, and put it in the state folder with `utils.fileEntry`. Use a derivation only when the input is YAML or another format that Nix can't read. Give a task that reads a generated file `after = [ "devenv:files" ]`.

### Layout

Read each file from the top to the bottom. The main part of the file is the last block.

1. The argument set, then `utils`.
2. Constants that the file uses more than once.
3. Helper functions, each after the values it uses.
4. Parts that the main block uses more than once, such as a `mkTaskChain` result.
5. The main block: `profiles.<name>`, `processes`, or `services`.

- Bind a name in `let` only if the file uses it at least twice. Write a value that the file uses once where it's used.
- In a profile `module`, keep this order: `imports`, `env`, `packages`, `languages`, `scripts`, `git-hooks`, `tasks`, `enterTest`.
- In `tasks`, put the aggregate tasks (`lint:all`, `test:all`) last.
- Always declare processes and tasks. Don't gate them on `config.devenv.isTesting`, so `devenv up` and `devenv test` run the same stack.
- Don't give a local function the name of a helper in `nix/utils.nix`. Name it for what it builds, such as `mkNginx`.

### Documentation

- Write the documentation of a function as a doc comment, as [RFC 145](https://github.com/NixOS/rfcs/blob/master/rfcs/0145-doc-strings.md) defines. Start it with `/**` and put it directly before the function.
- Document functions only. Don't document a variable, a set of values, or a module.
- Write the content as CommonMark.
- Start with one sentence that says what the function returns. Then use these headings when they apply: `# Inputs` with one term for each argument, and `# Type` with a type signature in a code block.
- Write the type in the form `name :: { key :: Type, optional :: Type? } -> Result`.
- Don't add a comment that explains how code works. Name the value or split the function instead.
- Document each function of a set where it's defined, as `probes` does.

````nix
/**
  A `ready` check that passes when the command exits with 0.

  # Inputs

  `command`
  : The command, as a list of arguments.

  # Type

  ```
  command :: [String] -> AttrSet
  ```
*/
command = command: mkProbe (lib.escapeShellArgs command);
````

### Tests

Run `devenv --profile <name> test` for each profile that a change touches. The first run can fail when a hook formats a file. Run it again.

## SQL

Templates live in `sync/src/sync/templates/<backend>/`. Helm job templates live in `helm/files/templates/postgres/`. See [SQL Templates](docs/sql.md).

### Code

- Build SQL mappings with `Identifier()` and `Literal()`. Use parameters for runtime values.
- Never put user values into SQL text.
- Use `ON CONFLICT ... DO UPDATE` for upserts.
- Use `timestamptz`, `jsonb`, and `bigint GENERATED ALWAYS AS IDENTITY`.
- Use one transaction per logical operation, with the `atomic()` helper.
- Use `SECURITY DEFINER` only on purpose. Grant `EXECUTE` to the required role only.
- Mark read-only functions `STABLE`.
- Reload PostgREST with `NOTIFY pgrst, 'reload schema'` through `reload_postgrest()` after serving definitions change. Don't restart the Deployments.
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

### Console migrations

- The migrations of the `console` schema live in `console/migrations/NNNNN_name.sql`, and `goose` applies them. Add a new numbered file for each change. Don't edit a migration that another install applied.
- `lint:sql` runs `sqlfluff` on the files, with a limit of 88 columns, and `goose validate`.
- Give a `SECURITY DEFINER` function a fixed `search_path`, as `data_proxy.sees` does.
- Check access with `rbac.can()` and `rbac.mine()`. Both refuse if the rule is missing.

### Tests

- Put test SQL in `sync/tests/templates/<backend>/`, with the same headers and names as `src/` templates. Inline only one short statement, such as `SELECT set_config(...)` or `SET ROLE`.

## Permissions

Add a permission in these steps:

1. Add it under `[permissions.<object>]` in `helm/files/config/rbac.toml`, and give it to a role.
2. Call `rbac.can('<name>')` in the SQL that needs it.
3. Run `devenv --profile checks tasks run lint:toml`. It checks the file with `taplo` and the JSON schema.
4. Add a role test in `sync/tests/unit/test_templates.py`.
5. Update the permission tables in `docs/security/permissions.md`.

The `configure-db` job runs `verify-rbac` and fails if the database differs from the file.

## TypeScript and Svelte

The console is a SvelteKit app in `console/ui/`. The sources are the [Svelte best practices](https://svelte.dev/docs/svelte/best-practices), the [typescript-eslint configurations](https://typescript-eslint.io/users/configs) and the [Google TypeScript guide](https://google.github.io/styleguide/tsguide.html). ESLint and `svelte-check` enforce the rules below, so a rule that a tool can't check is the exception.

### Architecture

- The browser never calls PostgREST. A `load` function or a form action in `console/ui/src/routes` calls it through `lib/server/postgrest.ts`, with the token of the session. Keep the token and every secret in `lib/server/`.
- Keep the logic that needs a test in a module with no import of `$app` or `$env`, so that Vitest runs it. Pass the settings and `fetch` as arguments.
- Map a status to a message in one place (`postgrest.ts` and `messages.ts`), so that a page never writes its own text for a refusal.

### TypeScript

- Use `strict` and `noUncheckedIndexedAccess`. Lint with the typescript-eslint `strictTypeChecked` and `stylisticTypeChecked` configurations.
- Don't use `any`, the non-null `!`, or a type assertion. Only `as const` is allowed.
- Type a value where it enters the program, with a valibot schema: a PostgREST answer, a response of the identity provider, a cookie, a request body, a form field or a message from a frame. Derive the type with `v.InferOutput`. `lib/types.ts` contains the schemas of the rows, so a column that a view loses fails at the first read, with the field in the message.
- Use `type` for object types. Use `interface` only where a library needs declaration merging, such as `App.Locals` and `Window`.
- Use named exports. A `.svelte` file and a SvelteKit route file are the only exceptions.
- Return a `Result` for a failure that the caller can handle, such as an answer of PostgREST. Throw for an input that the program can't run without.
- Write no comment inside a function. Write a one-line `/** */` summary above an exported function only when its name doesn't say enough. A lint disable comment gives the reason. A marker that a tool reads, such as `// pragma: allowlist secret`, is allowed.

### Svelte

- Use runes only. Use `$state` for a value that changes what the page shows, and a plain variable for the rest.
- Compute a value with `$derived`. Don't copy a prop or `data` into `$state` in an `$effect`: derive from the prop, and keep only the changed part in `$state`.
- Use `$effect` only for a system outside Svelte, such as a timer or a listener, and return its cleanup. Use `{@attach}` to sync a DOM element, as `Modal.svelte` does.
- Treat props as values that change. Derive a value that depends on a prop.
- Use a keyed `{#each}` with a key that's unique to the item. Never use the index.
- Write events as attributes (`onclick`). Don't use `on:`.
- Pass every internal `href` through `resolve()` from `$app/paths`. For a query string, use a `GET` form.
- Write each repeated piece of markup once, as a component in `lib/components/`. A component that belongs to one page stays in the folder of that route. A `.svelte` file has at most 80 lines of script and 150 lines of template.
- Use `use:enhance` on every POST form, except sign-out. A GET form (a filter or a pager) doesn't use it. Give every input a visible label (`Field` and `Check`). An input in a table row uses `aria-label`, because its column gives the visible name.
- Give every POST form a `Submission` from `lib/enhance.svelte.ts` and a `SubmitButton`, so the form shows its pending state and sends one request. A form action returns `{ toast }` when it works. A dialog shows a refusal inside itself (`inline`), and the page shows the refusal of any other form.
- Ask for a confirmation before a delete (`FormDialog` with `danger`), and hide the row until the answer comes (`begin` of the `Submission`).
- Use `FilterForm` for the filters of a list and `Time` for a time. Don't write a timer in a page. Reload live data with `poll(key)` and `depends(key)` from `lib/poll.svelte.ts`.
- Style with Tailwind and DaisyUI classes. Import icons by name from `@lucide/svelte`.

### Tooling

```bash
devenv --profile console tasks run lint:console
```

`lint:console` runs `svelte-check`, ESLint (`eslint-plugin-svelte` recommended and typescript-eslint) and `prettier --check`, for `console/ui/` and `console/notebooks/`. Run `pnpm format` in the folder to format. prek runs the hooks `prettier` and `eslint` at commit time. They use the binaries of `console/ui/node_modules`, so run `pnpm install` once.

### Tests

- Test a unit with Vitest, in a `*.test.ts` file next to the module. Use a table of cases with a name for each case.
- Write a browser test as a `playwright-bdd` scenario that a user could read. A step does one thing.
- Keep scenarios independent, so they can run in parallel. A scenario creates the data that it needs, with a name that no other scenario uses. A scenario that changes data that others read goes to `tests/features/shared/`. A scenario that adds a schema or a table has the tag `@new-schema`, writes each name with the step parameter `{name}`, and signs in as `web_root`. The parameter adds a random suffix, so the scenario and its repeats run in parallel.
- A toast that an earlier step left on screen matches a later wait for the same text. Wait for "Saved." with `saved()`, or wait for the row that the action creates.
- Wait for a condition, never for a time. Use a web-first assertion (`expect(...).toBeVisible()`), `expect.poll`, or the `hydrated` helper. A wait of a fixed time is allowed only to check that something doesn't happen.
- Write no test for the notebooks (`console/notebooks/`): no unit test, no browser test, and no scenario that opens the notebook.
- Hand state from one step to the next through the typed `world` fixture in `console/ui/tests/fixtures.ts`. Don't attach it to `page`.

## Notebooks

- A notebook never stores a token, a cookie or a secret. It gets its data from the grant that the console sends, and it reads only the links in that grant.
- Give every variable of a marimo cell a name that no other cell uses. A duplicate stops the whole notebook.
- Check the origin and the source of every message in `console/notebooks/src/bridge.ts`, and send a message to one exact origin, never to `*`.

## Documentation

- Use Simplified Technical English: short sentences, active voice, one idea per sentence.
- Keep a sentence at 25 words or fewer and a paragraph at 6 sentences or fewer. Split a longer text into a list or a table.
- Expand an acronym at its first use on a page, and define each project term once in `docs/glossary.md`. Add a row to its Tools table for each new tool.
- Use the fixed terms `access policy` (the table), `row policy` (the PostgreSQL rule) and `sync run`. Write `contains`, `stores` or `has`, and not `holds`. Write `immediately`, and not `at once`. Vale enforces the word swaps and the ban on semicolons.
- Give a diagram one owner page. Other pages link to it and don't embed a copy.
- Give every diagram a short name, and write it as the alt text of the image. Don't add a caption line under the image.
- Draw one dependency diagram of the whole system, on `docs/architecture/index.md`. Draw sequence diagrams for what happens in order, and put the diagrams of a layer in `docs/diagrams/architecture/`. The pages of the serving, sync and console layers have sequence diagrams only. A page about another topic may have a small dependency diagram. Use a table for anything else.
- Don't write by hand a table that a file already defines. `docs/security/roles.md` has a role table that `docs:build:roles` writes from `rbac.toml`, and `lint:roles` checks it.
- Write the documents in `docs/`. The home page is `docs/index.md`, and each folder has an `index.md` that lists its pages.
- Link to another page with a relative Markdown link: the path to the `.md` file and the slug of the heading (lower case, spaces as hyphens), as in `[Roles](../security/roles.md#roles)`. Link to a folder with its `index.md`. Marksman completes and follows these links in the editor, but it doesn't flag a broken one.
- Every page ends with a navigation block (Previous, Next, Index and Home) that `docs:build:nav` writes. Don't edit the block by hand. `docs/.nav.json` has the page order. Add a new page to that file and link it from its parent page. `lint:nav` fails on a missing page, a page without a link from its parent, or an old block.
- Write a diagram in D2 in `docs/diagrams/<name>.d2` or in a folder inside it, such as `docs/diagrams/schemas/`. The file `db.d2` imports the other files of its folder, so edit a table in its own file and not in `db.d2`. Render it with `devenv --profile checks tasks run docs:build:diagrams`, commit the `.d2` and the `.svg`, and embed the SVG with an image link and an alt text. Don't write a diagram in another language. `lint:diagrams` fails when an SVG is out of date. Every diagram renders in a light and a dark theme. Draw a database diagram with `sql_table` shapes and take the columns from the SQL files.
- Name a file with one word, and use two words only if one word is unclear.
- Give each topic one page, and link to it from the other pages. Keep a file near 800 words. Split a larger file by topic.
- Keep one topic in one document. Link to the owner. Don't copy.
- Use generic examples unless a real value is required.
- Say "the source" for the product, and name BigQuery only on a page about the BigQuery adapter or for a value that's specific to it.
- Verify claims against source, Helm templates, tests, or a command you ran.
- Vale runs only in the `vale` git hook. It checks the staged `*.md`, `*.py` and `*.nu` files when you commit. Run `vale <file>` in the `checks` shell to check a file earlier.
- Use contractions in Markdown. The Python and Nushell checks turn that rule off, because messages keep their full forms.
- Vale flags the acronym for a web address (write address), some adverbs, and acronyms in headings. Rephrase the sentence instead of ignoring the message.
- `DataProxy.Terms` fixes the casing of product names: PostgreSQL, PostgREST, SeaweedFS, DuckDB, BigQuery. Add a word to `.vale/styles/config/vocabularies/data-proxy/accept.txt` at the end of the file only.

## Git

- Use Conventional Commits: `type(scope)!: imperative summary`, lowercase, no trailing period. The scope is optional.
- Types: `feat` (new user-visible capability), `fix` (user-visible defect), `refactor` (internal restructuring), `test` (test-only work), `docs` (documentation-only work), and `chore` (CI, tooling, dependencies, and maintenance).
- Don't use `perf` or `ci`: classify release-worthy performance work as `feat` or `fix`, and classify CI work as `chore`.
- Release levels: a type with `!` bumps major. `feat:` bumps minor. `fix:` bumps patch. All other types create no release. `cog` (cocogitto) applies these rules, and `cog.toml` has its settings.
- Use `!` only with `feat` or `fix` release commits. Explain the migration in the body. Don't use a `BREAKING CHANGE` footer.
- Don't edit `CHANGELOG.md` by hand. The release step writes it from the commits, and it lists only breaking changes, features and fixes.
- Make one logical change per commit. Squash fixups before merge.
- The hooks run in prek at commit time. If a hook rewrites a file, stage the file again and commit again.
- `ripsecrets` blocks a line that looks like a secret. Add a whole file to `.secretsignore`, or mark one line with `pragma: allowlist secret`, as `helm/tests/ducklake_catalog_test.yaml` does.
- Never commit or push without explicit approval. Stage, summarize, and wait.

```text
feat(sync)!: require DBOS workflow recovery

Sync recovery now requires the DBOS worker and workflow state in PostgreSQL.

Migration:
- Deploy the DBOS worker before you enable scheduled syncs.
- Verify the DBOS system database is reachable.
```
