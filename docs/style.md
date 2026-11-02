# Style

These rules cover the code and the docs of this repository. Read the section for the files that you change. [AGENTS.md](../AGENTS.md) maps each path to its section. The [style guide](../STYLEGUIDE.md) has the rules that matter most and the definition of done.

## The rule format

Each rule is one line: `- **PY-07** The rule. [must, ruff]`.

1. The ID has a prefix for the area and a number. Cite the ID in a review or a commit message.
2. `must` means the change can't merge when it breaks the rule. `should` means break it only with a reason.
3. The last word of the tag names what checks the rule: a tool, a devenv task or `review`.
4. A rule has at most 40 words, and an area has at most 2000 words. `docs:lint:style` checks the format and that each cited ID exists.

## The areas

| Area | Prefix | Rules for |
| --- | --- | --- |
| [Principles](#principles) | GEN | Every file, and the state of the system |
| [Logs](#logs) | LOG | Every language that logs |
| [Tests](#tests) | TEST | Every test |
| [k6](#k6) | K6 | `k6/**` |
| [Python](#python) | PY | `sync/**/*.py` |
| [Lua](#lua) | LUA | The proxy modules, the specs, the design, the types and the comments |
| [Nushell](#nushell) | NU | `scripts/*.nu`, `helm/files/scripts/*.nu` |
| [Nix](#nix) | NIX | The names, the layout, the tasks and the doc comments |
| [SQL](#sql) | SQL | Templates, migrations and permissions |
| [TypeScript](#typescript) | TS | `console/ui/src/**/*.ts` and the browser tests |
| [Svelte](#svelte) | SV | `console/ui/src/**/*.svelte` |
| [Notebooks](#notebooks) | NB | `console/notebooks/**` |
| [Documentation](#documentation) | DOC | `docs/**`, `README.md` and the diagrams |
| [Git](#git) | GIT | Commits, releases and hooks |

## Principles

Applies to: the whole repository.

These rules apply to every file. The pages for each area add more.

### Working rules

- **GEN-01** Read the code before you write code. [must, review]
- **GEN-02** Follow an existing pattern before you add a new one. [must, review]
- **GEN-03** Prefer deletion to a new abstraction or a new configuration layer. [should, review]
- **GEN-04** Make small, reviewable changes. Don't bundle unrelated work. [should, review]
- **GEN-05** Fix the root cause, not the symptom. [must, review]
- **GEN-06** Keep one source of truth for each fact. [must, review]
- **GEN-07** Use the idiomatic tool for each ecosystem. [should, review]

### State

- **GEN-08** Let DBOS and PostgreSQL own the orchestration state. Redis is a cache only. [must, review]
- **GEN-09** Keep DBOS workflows deterministic, and put I/O in steps. [must, review]
- **GEN-10** Don't re-implement DBOS checkpoints. [must, review]
- **GEN-11** Write no comment inside a function body. Put the reason in the comment above the function, or use a name. The check covers Nushell and TypeScript. [must, code]
- **GEN-12** Start a file with at most one header comment of 40 lines or fewer. It says what the file does and how to run it. Put the detail of a function above the function. [must, code]
- **GEN-13** Write a `{{/* */}}` comment above each `define` of the Helm chart. It says what the helper returns, and it names the keys of a dict input. [must, code]

See [Architecture](architecture/index.md) for the layers. See [Database Schema](database.md) for roles, row policies and privileged functions.

## Logs

Applies to: all first-party code that logs from a deployment.

### Logs in every language

- **LOG-01** Write lifecycle phrases that a person can read: `<Component> <action> <state>: <relevant context>`. [must, review]
- **LOG-02** Use `debug` for probe detail, `info` for normal lifecycle changes, `warning` for retries or skipped optional work, and `error` only before a terminal error. [must, review]
- **LOG-03** Never log credentials, tokens, connection strings, request bodies, cache values, SQL values, or query strings. [must, review]
- **LOG-04** Don't use `print` for production logs. A documented machine-output protocol may write to stdout when callers parse it. [must, ruff]
- **LOG-05** Keep the output of external tools as it is. Don't parse or reformat the output of `rclone`, `psql`, `pg_dump`, `duckdb` or `kubectl`. [must, review]

```text
S3 bucket is not ready; retrying: bucket=test-bucket error=connection refused
```

### Logs in Python

- **LOG-06** Use the standard-library logger from `sync.log`. Don't configure handlers in application modules or create module-specific loggers. [must, review]
- **LOG-07** Set the log level only through `Settings.LOG_LEVEL`. Don't set a level in application modules. [must, review]
- **LOG-08** Keep the DBOS workflow, schema and table context through the context values in `sync.log`. [must, review]

```python
logger.info("DuckLake commit completed: published_tables=%d", len(published_tables))
```

### Logs in Lua

- **LOG-09** Write logs only through `log.write`. Don't call `ngx.log` in other modules. [must, review]
- **LOG-10** Use the fixed messages in `constants.log.messages`, and add the context as `name=value` pairs. [must, review]
- **LOG-11** Use `log.note` for an event that has no request, such as a timer or the Redis store. [must, review]

### Logs in Nushell and k6

- **LOG-12** In Nushell, use `std/log` directly. A command in `scripts/` may use `print` for machine output, such as a CI output line. It may also use `print` for a log stream that it shows to a person. [must, review]
- **LOG-13** In k6, write log lines with `info` and `warn` from `k6/lib/log.js`, with an event name and `key=value` fields. Never use `console`. [must, review]

## Tests

Applies to: every test, in `sync/`, `proxy/`, `console/`, `helm/` and `k6/`.

### Levels and tasks

- **TEST-01** Put each test at the lowest level that proves the behavior. Don't repeat a check at a higher level. [must, review]
- **TEST-02** Make a unit test check one unit exhaustively: a function, a class, a method or an SQL template. Cover every input, every branch and the edge cases. [must, review]
- **TEST-03** Make an integration test check one behavior that can cross units or services. Examples are a state commit, a catalog read and a request through the proxy. [must, review]
- **TEST-04** Don't let the infrastructure decide the level. A template test on the real PostgreSQL server is a unit test, because it checks that template alone. [must, review]
- **TEST-05** Name the task type by what the test needs. A `unit` task needs no service, and an `int` task starts services. [must, devenv]
- **TEST-06** Mark a test that needs the PostgreSQL server with `postgres`. `sync:test:unit` skips it, and `sync:test:int` runs it. [must, devenv]
- **TEST-07** Call no service in a unit test, except the engine of the template that it tests: the PostgreSQL test server or an in-memory DuckDB. [must, review]
- **TEST-08** Don't mock PostgreSQL in an integration test. Mock only external services, such as BigQuery. [must, review]
- **TEST-09** Use E2E tests only for what needs the deployed stack. [should, review]

| Level | Location | Task | Covers |
| --- | --- | --- | --- |
| Unit | `sync/tests/unit/`, `proxy/tests/unit/`, `helm/tests/` | `sync:test:unit`, `proxy:test:unit`, `chart:test:unit` | One behavior of a function, class, method or template |
| Integration | `sync/tests/integration/` | `sync:test:int` | Flows that cross the database, the cache or the storage |
| Integration | `proxy/tests/integration/` | `proxy:test:int` | The request path: routing, retry, cache, filter pushdown |
| Unit | `console/ui/src/**/*.test.ts` | `console:test:unit` | One module of `lib/` and `lib/server/` |
| Integration | `console/ui/tests/` | `console:test:int` | The console as a user sees it |
| E2E | `k6/e2e.js`, `k6/scenarios/` | `cluster k6 e2e` | The deployed k3d cluster |

### Writing tests

- **TEST-10** Keep a test module for tests only. Put fixtures in `sync/tests/fixtures/` and test-double dataclasses in `sync/tests/fixtures/types.py`. Put builders in the module of `sync/tests/helpers/` for their area (`core`, `state`, `dbos`, `ducklake`, `routing`, `rbac` or `console`). [must, review]
- **TEST-11** Merge small test files that cover one module. [should, review]
- **TEST-12** Test your own code and no code of a third party, such as DBOS, PostgREST, Keycloak, Kubernetes, Redis, a cloud SDK or a browser engine. [must, review]
- **TEST-13** Stub the call at the edge of your code, and test what your code does with the answer. [must, review]
- **TEST-14** Use table-driven cases with an `id` that names the scenario. Use Hypothesis only for invariants over generated inputs. [must, review]
- **TEST-15** Use `pytest-bdd` for integration behavior that crosses the database, the cache or the storage. [should, review]
- **TEST-16** Put feature files in `sync/tests/integration/features/` and step definitions in `sync/tests/integration/steps/`. [must, review]
- **TEST-17** Assert exact values. Don't use a substring check, except a regex against generated text when an exact assertion is brittle. [must, review]
- **TEST-18** Don't assert the full contents of a registry that grows. [must, review]
- **TEST-19** Don't test field storage, defaults, or "no exception was raised." [must, review]
- **TEST-20** Fix a flaky test at its cause, and add a test that reproduces it. Never add a retry. [must, review]

```python
@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        pytest.param(RangeSelection(partition_id="10", column="id", lower=10, upper=20),
                     '("id" >= 10 AND "id" < 20)', id="range"),
    ],
)
def test_renders_exact_predicate(selection, expected):
    assert render(partition_condition(partition_for(selection))) == expected
```

A test module that has only tests, and a helper that lives in a module of `helpers/`:

```text
✅ sync/tests/helpers/state.py      def build_plan(...)
✅ sync/tests/unit/test_plan.py     def test_plan_groups_changed_tables(): ...
❌ sync/tests/unit/test_plan.py     def build_plan(...)   # a builder in a test module
```

## k6

Applies to: `k6/**`.

### Layout

- **K6-01** Put every helper in a module of `k6/lib/`, and let `k6/lib/index.js` re-export it. [must, review]
- **K6-02** Keep only the steps in a scenario file. Keep only the orchestration and the iteration functions in `load.js`. [must, review]
- **K6-03** Make one check assert one condition. [must, review]
- **K6-04** Put a blank line between two statements when one of them spans several lines. [should, review]

### Types

- **K6-05** Write the k6 files as plain JavaScript with JSDoc types, and let `tsc` check them. [must, review]
- **K6-06** Declare all types in `k6/lib/types.d.ts`: the k6 modules and the shapes of the code. [must, review]
- **K6-07** Use a shape such as `Step` or `TokenData` in a `@param` without an import. The shapes are global types. [must, review]
- **K6-08** Write a new type in `types.d.ts`, and give every function parameter a `@param` type. [must, review]
- **K6-09** Keep `types.d.ts` a script. A top-level `import` or `export` turns it into a module, and the declarations of the k6 modules fail. [must, review]
- **K6-10** Write `import("k6/http").Response` inside `types.d.ts` when it needs a k6 type. [must, review]
- **K6-11** Never give a `.d.ts` file the name of a `.js` file in the same folder, because `tsc` then skips the `.js` file. [must, review]
- **K6-12** Type external data at its source with a type guard such as `isRecord` or `recordsOf` for JSON. [must, review]
- **K6-13** Don't cast. Read an unknown value with `String`, `Number` or a type guard. [must, code]

See [Logs](#logs) for the log lines of k6 (LOG-13).

## Python

Applies to: `sync/**/*.py`.

### Code

- **PY-01** Type a password, a key or an address with a password as `SecretStr`, and call `get_secret_value()` only where the code connects. [must, review]
- **PY-02** Give a credential no default. [must, review]
- **PY-03** Use dataclasses for plain data. Use Pydantic models only for validation at boundaries. [must, review]
- **PY-04** Model closed alternatives as tagged unions, and match every case. Use `_` only for open sets. [must, review]
- **PY-05** Don't prefix internal names with `_`. Use `_` only for a required but unused parameter. [must, review]
- **PY-06** Keep `__init__.py` to one docstring line, and import from submodules. [must, ruff]
- **PY-07** Inline a one-line helper that has one caller. [should, review]
- **PY-08** Put a function in the module of its domain. Use `utils.py` only when no domain fits. [must, review]
- **PY-09** Write a multi-stage flow as a context dataclass with one method per stage, like `PlanningContext` in `planning.py`. [should, review]
- **PY-10** Use explicit parameters and named functions. Avoid `**kwargs`, opaque lambdas, `Any` and `object`. [must, ruff+pyrefly]
- **PY-11** Run blocking calls through `asyncer.asyncify`. [must, review]
- **PY-12** Treat JWT claims as identity only, and read authorization state from the database. [must, review]
- **PY-13** Don't make outbound HTTP calls in request-time authorization. [must, review]

```python
@dataclass
class PlanningContext:
    config: SyncConfig
    changed: dict[str, str] = field(default_factory=dict)

    async def detect(self) -> None: ...
    async def expand(self) -> None: ...
    def group(self) -> SyncWork: ...
```

### Data and sync

- **PY-17** Open one DuckDB connection for each concurrent task, and set its memory, threads and spill folder with the `limits` template. A connection serves one query at a time. [must, review]
- **PY-18** Write Parquet row groups of at most 10,000 rows and 32 MB (`PARQUET_ROW_GROUP_SIZE` and `PARQUET_ROW_GROUP_BYTES`), so that a dump of wide tables fits in memory. [must, review]
- **PY-19** Reconcile the serving views of every table of a planned schema, also when a run plans no change. [must, review]
- **PY-20** Parse and compute times with `whenever` (`Instant`, `PlainDateTime`). Import `datetime` only at a boundary that needs it. [should, review]

### Tooling

- **PY-14** Keep `sync:lint:py` green. It runs `ruff` with `select = ["ALL"]`, `pyrefly` with `preset = "all"`, `complexipy` with a limit of 10 for each function, and `vulture`. [must, ruff+pyrefly]
- **PY-15** Use `cast` once, in `templates.py`, where it marks the text of a repository template as `LiteralString`. [must, pyrefly]
- **PY-16** Write one long string and not an implicit string concatenation. `E501` is off. [must, ruff]

See [Logs](#logs) for the logging rules of Python (LOG-06 to LOG-08).

## Lua

### Lua layout, tests and checks

Applies to: `proxy/src/proxy/**`, `proxy/tests/**`, `proxy/lux.toml`.

The proxy is OpenResty and Lua code in `proxy/src/proxy`. It runs in the pinned OpenResty image.

#### Layout and dependencies

- **LUA-01** Keep one named module for each concern: `cache`, `config`, `constants`, `handler`, `limiter`, `log`, `main`, `parser`, `postgrest`, `redis`, `regex`, `request`, `response`, `schemas`, `settings`, `sync` and `utils`. [must, review]
- **LUA-02** Write a module in this order: imports, local variables, local functions, classes, and the returned object (the module table, the functions on it, and `return`). [must, review]
- **LUA-03** Put a local variable that needs a local function right after that function. [must, review]
- **LUA-04** Declare the module table with the other local variables when nothing else needs it later. [should, review]
- **LUA-05** Name modules `proxy.<name>`. Let `nginx.conf` load only `proxy.main`. [must, review]
- **LUA-06** Return the module table directly. Don't add re-export modules. [must, review]
- **LUA-07** Read the `PROXY_*` variables only through the table that `config.load` (`proxy/src/proxy/config.lua`) returns. [must, review]
- **LUA-08** Let only `main` load the config, once at start-up in `main.init`, and pass it down as a parameter named `config`. [must, review]
- **LUA-09** Don't call `os.getenv` or read `ngx.var` for a setting in another module. [must, code]
- **LUA-10** Declare each setting, with its type, default and limits, in `proxy/src/proxy/schemas/definitions/config.lua`. List its `PROXY_` variable in `helm/files/config/nginx.conf` with an `env` directive. [must, review]
- **LUA-11** Use the module `settings` only for the `sync.json` table that every worker shares and that the console replaces. [must, review]
- **LUA-12** Define every named constant in `constants.lua`, in a group with lower case names, such as `constants.redis.connect_timeout_ms`. Other modules define no constants. [must, code]
- **LUA-13** Describe each constant group with a small type in `constants.d.lua`, and compose the `Constants` type from them. [must, review]
- **LUA-14** Define every Lua pattern that matches text in `regex.lua`, named after what it matches, such as `regex.parameter`, with a `---@field` line in `regex.d.lua`. [must, review]
- **LUA-15** Don't write such a pattern at the point of use. A single character stays in its `gsub` call, and a filter grammar stays in `parser/patterns.lua` as LPeg. [must, review]
- **LUA-16** Write grammars for query parameters with LPeg, in `parser/patterns.lua`. Don't split strings by hand. [must, review]
- **LUA-17** Use `ngx` APIs and pinned `lua-resty-*` modules. [must, review]
- **LUA-18** Declare every Lua library in `proxy/lux.toml`. Change versions only with `lx add` (`--test` for test libraries) or `lx update` in `proxy/`, and commit `proxy/lux.lock`. [must, review]
- **LUA-19** Don't list `busted` in `lux.toml`. Lux installs it, and `stylua` and `luacheck`, on its own. [must, review]
- **LUA-20** Add a rock only for a module that OpenResty lacks. OpenResty already has `resty.redis`, `resty.string`, `resty.lock`, `resty.sha256` and `cjson`. [must, review]
- **LUA-21** Don't add LuaRocks steps to `Containerfile.proxy`. The image runs the locked `lx build --only-deps` and fails if `lux.lock` changes. [must, review]
- **LUA-22** Keep the rocks path the same in `Containerfile.proxy`, `helm/files/config/nginx.conf` and `helm/tests/proxy_config_test.yaml`. [must, review]
- **LUA-23** Preserve the request-header allowlist and the cache-key isolation by identity, schema, representation, range and DuckLake snapshot. [must, review]
- **LUA-24** Don't change the cache key or the cached value format without a migration plan. Old and new proxy versions share Redis during a rolling upgrade. [must, review]

#### Tests

- **LUA-25** Write one test file for each module, `proxy/tests/unit/<module>_spec.lua`, and keep `proxy/tests/unit` flat. `constants`, `regex`, `main` and `handler` have no test file. [must, review]
- **LUA-26** Write table-driven Busted specs, with one `describe` for each function and `it(case.name)` for each case. [must, review]
- **LUA-27** Give each case a name that states the behavior, for example `retries a 503 on the retry service`. [must, review]
- **LUA-28** Keep fakes and fixtures in `proxy/tests/unit/helpers.lua` and `proxy/tests/unit/fixtures`. Make a fake follow the shape of the real API, such as the 32 raw bytes that `resty.sha256` gives. [must, review]
- **LUA-29** Test pure functions only. A function with an effect, such as a request, a socket or an nginx phase, belongs to the Hurl suite in `proxy/tests/integration/` or to k6. [must, review]
- **LUA-30** Compare a rewrite with the old code on random inputs before you delete the old code. [should, review]

#### Checks

- **LUA-31** Run `devenv --profile proxy tasks run proxy:lint:lua proxy:test:unit` before review. [must, devenv]
- **LUA-32** Keep `proxy:lint:lua` green: `luacheck` (line length 120, complexity 10) with `lx lint`, and the LuaLS type check at the Hint level with no problems. [must, luacheck+luals]
- **LUA-33** Let the git hook run `lx fmt` (StyLua), and use tabs. [must, stylua]
- **LUA-34** Restore `proxy/lux.lock` with `git checkout -- proxy/lux.lock` after a sync that only reorders it. [should, review]
- **LUA-35** Start the editor inside the devenv shell, so that LuaLS can read `LPEG_TYPES`. [should, review]

### Lua code

Applies to: `proxy/src/proxy/**/*.lua`.

#### Design

- **LUA-36** Write a pure core and a thin shell. A pure function gets all its inputs as arguments, such as the configuration and the current time, and returns a new value. [must, review]
- **LUA-37** Let only `main.lua`, `handler.lua`, the `Redis` class in `redis.lua`, `postgrest.send`, `log.write` and `response.send` touch `ngx.var`, `ngx.req`, `ngx.header`, `ngx.now`, `ngx.log` or the network. [must, review]
- **LUA-38** Use pure `ngx` string helpers, such as `ngx.escape_uri` and `ngx.decode_base64`, in the core. [should, review]
- **LUA-39** Keep module state only in `main.lua`, and the loaded schemas in the registry (`schemas/main.lua`). Other modules keep no state. [must, review]
- **LUA-40** Build new tables. Don't change an argument. [must, review]
- **LUA-41** Split a decision from its effect. Write `plan` to decide and `send` to act, as `response.plan` and `response.send` do. [must, review]
- **LUA-42** Inject external I/O at module boundaries, such as the `sender` and the `store`, so that unit tests can use fakes. [must, review]
- **LUA-43** Use an object with methods only for state or I/O, such as the cache store. Use plain functions and tables for everything else. [must, review]
- **LUA-44** Use a plain loop only for an index scan or ordered side effects. [should, review]
- **LUA-45** Use guard clauses and lookup tables instead of long `if` chains. [should, review]
- **LUA-46** Keep each function under the `luacheck` complexity limit of 10. Split a longer function into named steps. [must, luacheck]

#### Errors and names

- **LUA-47** Don't use `assert` in code under `proxy/src`. Check the value, and raise an error with a message. Specs can use `assert`. [must, code]
- **LUA-48** Return `value, err` for an error that the caller can handle. Raise an error for an input that the proxy must not run without, such as a missing file or setting. [must, review]
- **LUA-49** Use `pcall` only around an external boundary. Don't use it to hide a bug. [must, review]
- **LUA-50** Give every number and every repeated text a name in `constants.lua`. Don't put a bare number or a repeated string in code. [must, review]
- **LUA-51** Put a helper that works on plain values and knows nothing of its module in `utils.lua`, with its types in `utils.d.lua`. [must, review]
- **LUA-52** Keep a private helper that's coupled to its module in that module, as a local function. [must, review]

```lua
✅ local timeout = constants.redis.connect_timeout_ms
❌ local timeout = 1000
```

#### Penlight

- **LUA-53** Use Penlight for data, strings and classes. Use `pl.List` for arrays, `pl.tablex` for maps and searches (`pairmap`, `find_if`, `foreach`, `update`), and `pl.stringx` for text (`strip`, `startswith`, `split`). [must, review]
- **LUA-54** Cast every Penlight `require` to its module class: `pl.ClassModule`, `pl.ListClass`, `pl.tablex` and `pl.stringx`. Without the cast, LuaLS types the module from the rock source, which has no annotations. [must, luals]
- **LUA-55** Make every class with `pl.class`. A module table is a namespace, not a class. [must, review]
- **LUA-56** Declare the class type in the `.d.lua` file of its module, and derive it from `pl.Class`. Give the constructor an `---@overload` that returns the class type. [must, luals]
- **LUA-57** Don't use the IO modules of Penlight (`pl.file`, `pl.dir`, `pl.path`, `pl.utils.readfile`) in a request, because they block the nginx worker. Use them at start-up only. [must, review]

```lua
local tablex = require("pl.tablex") --[[@as pl.tablex]]

function postgrest.passed_headers(response_headers)
	return tablex.pairmap(function(_, name) return response_headers[name], name end, constants.headers.response)
end
```

### Lua types and comments

Applies to: `proxy/src/proxy/**/*.lua`, `proxy/src/proxy/**/*.d.lua`, `proxy/.types/**`.

#### Types

- **LUA-58** Describe every function with `---@param` and `---@return`, and every shared table with `---@class` and `---@field`. [must, luals]
- **LUA-59** Put the types of a module in `<module>.d.lua`, next to `<module>.lua`. Start the file with `---@meta`, and write no other file comment. [must, review]
- **LUA-60** Put a type in the file of the module that creates it. Don't write types in `<module>.lua`, except the `---@class` of the module table and the class of an object that the module implements with methods. [must, review]
- **LUA-61** Keep a class next to its `local` table, and declare no `---@field` for functions that the file defines. LuaLS reports `inject-field` for a function that a file adds to a class that another file declares. [must, luals]
- **LUA-62** Don't use `table` or `any` if a named type exists. [must, review]
- **LUA-63** Use `--[[@as Type]]` for a cast, and only at an external boundary. [must, review]
- **LUA-64** Don't use `---@diagnostic disable` in `proxy/src`. Fix the cause. A test file can disable `missing-fields`, because a case sets only the fields that it uses. [must, luals]
- **LUA-65** Add the type definitions of a library that has none to `proxy/.types/`. Load LPeg types from `LPEG_TYPES` and Penlight types from `PENLIGHT_TYPES`. [must, review]

#### Schemas

- **LUA-66** Define the fields of each schema in its own file, `proxy/src/proxy/schemas/definitions/<name>.lua`. The file returns only a table of field name to field type, built with `tableshape`. [must, review]
- **LUA-67** Let `schemas/schema.lua` contain the `Schema` class. It makes the checker (`types.shape(fields, { open = true })`) and gives `validate`. [must, review]
- **LUA-68** Call one field type a field, not a shape. Keep the word `shape` for the `types.shape` call. [should, review]
- **LUA-69** Let `schemas/main.lua` be the registry singleton. At start-up it registers each `<name>.lua` of `definitions/` as the schema `<name>`. [must, review]
- **LUA-70** Read a schema as a field of the registry, such as `schemas.cached_entry`, with no call. [must, review]
- **LUA-71** Make every checker open, because every schema describes data from outside that can have other fields. [must, review]
- **LUA-72** To add a schema, make the file, and add `---@field <name> Schema<T>` to `SchemasModule` in the `.d.lua` file of the module that owns the data type `T`. [must, review]
- **LUA-73** Read data that the proxy doesn't create (files, Redis entries, tokens, environment variables) only through `schemas.<name>:validate(value)`, at the boundary where it enters. [must, review]
- **LUA-74** Test the registry and every schema in `schemas_spec.lua`, table-driven: valid values, and invalid values with the field in the reason. [must, review]
- **LUA-75** For `config`, compare the result with a typed `Config` that has every field. A new field of `Config` makes the typed value incomplete, and the test fails if the definition has a field that the type lacks. [must, review]

#### Comments

- **LUA-76** Write a one-line `---` description above every `---@class` and `---@alias`. [must, review]
- **LUA-77** Write a `---` summary line above every function and every constant, in Simplified Technical English: short sentences, present tense, active voice. Write `if`, not `when`. [must, review]
- **LUA-78** Write comments as plain text. Don't use Markdown headings, tables, backticks, ASCII diagrams or section banners. [must, review]
- **LUA-79** Start each file with plain `--` lines that say what the module does and why. [must, review]
- **LUA-80** Show an example of a function in a fenced `lua` block with `--> result`. This is the only place for a code fence. [should, review]
- **LUA-81** Don't write comments inside a function body. Use a name for the idea instead. [must, review]

```lua
✅ local retry_after = wait_for_retry(response)
❌ -- wait before we try again
   local x = wait_for_retry(response)
```

## Nushell

Applies to: `scripts/*.nu`, `helm/files/scripts/*.nu`, `console/notebooks/*.nu`.

Nushell scripts run the Helm jobs and the local commands.

### Code

- **NU-01** Declare input and output types. Don't use `any` when a concrete type exists. [must, review]
- **NU-02** Use full flag names, such as `open --raw` and `save --force`. [must, nu-lint]
- **NU-03** Use `where` to filter, `match` to dispatch, and `get --optional` for optional fields. [should, nu-lint]
- **NU-04** Start a continuation line with the pipe, and put one step on each line. [must, review]
- **NU-05** Read an optional variable with `$env.NAME? | default value`. [must, review]
- **NU-06** Use `default {|| $env.NAME }` for a variable that a caller may not need, because `default $env.NAME` fails when the variable isn't set. [must, review]
- **NU-07** In `helm/files/scripts/`, run SQL only through `execute-sql` from `lib.nu`, with one template and one context record. Quote values with `quote-pg`. [must, review]
- **NU-08** Use one command for one kind of quote (`quote-identifier`, `quote-literal`). Don't pass the kind as a string. [must, review]
- **NU-09** Give each script one `main` command, and give each CronJob its own script. [must, review]
- **NU-10** Use pipelines for data transforms and `for` for ordered external side effects. [should, nu-lint]
- **NU-11** Add input and output types and `@example` blocks to an exported reusable helper. [should, review]
- **NU-12** Don't import modules across `helm/files/scripts/` and `scripts/`. They're separate codebases. [must, review]
- **NU-13** Run `nu --ide-check` on a changed file. [should, review]
- **NU-14** Write an expression that three places repeat as a helper (`app-schema`, `role-literals`). Keep one table for a list that several commands read (`IMAGES` in `scripts/lib.nu`). [should, review]

### Errors and files

- **NU-15** Stop a script with `fail $message` from `lib.nu`. It logs the message and raises an error with a label on the message in the caller. Don't call `error make`. `console/notebooks/build.nu` is standalone and keeps its own `fail`. [must, code]
- **NU-16** In `scripts/` and `helm/files/scripts/`, read and write files only through the helpers of `lib.nu`: `read-text`, `read-data`, `write-text`, `parse-json`, `parse-toml`, `parse-yaml`, `make-dir` and `remove-path`. [must, code]
- **NU-17** Wrap `cd` and `http get` in `try` at the call, because the helpers of `lib.nu` wrap only files. [must, review]
- **NU-18** Let an error of a builtin or an external command stop the script. Use `try` only to add context that the error lacks, such as the table that failed, or to clean up. [must, review]
- **NU-19** Use `check-result` for the record that `complete` returns, and `wait-until` for a wait. Don't write the exit-code check or the polling loop again. [must, review]

```nu
✅ fail $"The table ($name) is missing"
❌ error make { msg: $"The table ($name) is missing" }
```

### Tooling and tests

- **NU-20** Keep `nu:lint:scripts` green. It runs `nu-lint` and fails on a finding, because `nu-lint` exits with 0 in that case. [must, nu-lint]
- **NU-21** Give each rule that `.nu-lint.toml` turns off a comment with the reason. [must, review]
- **NU-22** Run `devenv --profile repo tasks run nu:test:unit` after a script change. `run.nu` finds every `test ...` command, so a new test needs no registration. [must, devenv]

```nu
use ./lib.nu [quote-pg execute-sql]

for schema in $schemas {
    execute-sql postgres/create_schema.sql {schema: (quote-pg $schema identifier)}
}
```

See [Logs](#logs) for the logging rules of Nushell (LOG-12).

## Nix

### Nix naming and code

Applies to: `**/*.nix`, `devenv.yaml`.

Nix files build the test setup of the devenv profiles. The profile of a component lives in `<component>/devenv.nix`, and the root `devenv.yaml` imports it. `nix/profiles/` contains the profiles that belong to no component. `nix/stack/` contains one part of the test stack for each file, and `nix/utils/` contains the helpers.

#### Naming

- **NIX-01** Name a task `<component>:<verb>:<target>`. [must, devenv]
- **NIX-02** Use one of these verbs: `install`, `build`, `lint`, `test`, `setup`, `fetch`, `clean`, `serve` or `open`. [must, devenv]
- **NIX-03** Name the target for the object of the verb, in the singular, such as `py`, `image` or `db`. For `test`, the target is the task type: `unit` or `int`. [must, review]
- **NIX-04** Name a task that groups other tasks with the component `repo`: `repo:lint` and `repo:test:unit`. `devenv tasks run repo` runs both. [must, devenv]
- **NIX-05** Run a group with a namespace prefix. `devenv tasks run sync:test` runs the sync tests of the active profile. [should, review]
- **NIX-06** Choose the component from three kinds. A deployable is `sync`, `proxy`, `console` or `chart`. A part of the test stack is `pg`, `pgrest`, `kc`, `s3` or `nb`. A repository tool is `nu`, `ci`, `ctr`, `docs`, `nix` or `repo`. [must, devenv]
- **NIX-07** Abbreviate a name that's long. Use the same short form in every layer: the task, the process, the profile and the test image. A file name keeps the full word. [must, review]
- **NIX-08** Use these short forms: PostgreSQL `pg`, PostgREST `pgrest`, Keycloak `kc`, SeaweedFS `s3`, notebooks `nb`, containers `ctr`, workflows `wf`, diagrams `diag`, provision `prov` and configure `conf`. [must, review]
- **NIX-09** Add a word to this list before you use a new short form. [must, review]
- **NIX-10** Keep the full words in environment variables and in the names of registry images, because the tests and the chart read them. [must, review]
- **NIX-11** Name a profile `<component>` for the whole stack of a component and `<component>-unit` for the part that needs no service. [must, review]
- **NIX-12** Name a profile by its job. `shared.nix` has `base` and `python`, which the others use. `repo` runs lint and unit tasks, and `integration` has the integration suites. `nix` checks the profiles, and `default` is the shell of a developer. [must, review]
- **NIX-13** Give a file in `nix/stack/` or `nix/profiles/` the full name of its part or profile, such as `postgres.nix` or `integration.nix`. [must, review]
- **NIX-14** Put a component profile in the folder of the component, and list the folder under `imports:` in `devenv.yaml`. Put an input that only that component uses in the `devenv.yaml` of the folder. [must, review]
- **NIX-15** Import `nix/profiles/*` in the root `devenv.nix`. [must, review]

devenv rejects a task name that breaks these rules when it evaluates a profile. The verbs and the components are in `nix/utils/naming.nix`.

```text
✅ sync:test:int        console:setup:db      repo:lint
❌ sync:tests:int       console:db            lint:all
```

#### Code

- **NIX-16** Format with `nixfmt`. Don't align by hand. [must, nixfmt]
- **NIX-17** Put every web address in quotes. Don't use a bare address. [must, review]
- **NIX-18** Don't use `rec`. Bind the shared names in a `let` block and use `inherit`. [must, code]
- **NIX-19** Don't use `with` at the top of a file. Use `with pkgs;` for a list of packages, or for one expression that uses three or more `pkgs` attributes, as `python.nix` does. [must, review]
```nix
✅ packages = with pkgs; [ curl goose nushell ];
✅ package = with pkgs; symlinkJoin { name = "uv"; paths = [ uv ]; nativeBuildInputs = [ makeWrapper ]; };
❌ with import <nixpkgs> { };
```

- **NIX-20** Don't use lookup paths such as `<nixpkgs>`. The lock file of devenv pins every input. [must, code]
- **NIX-21** Name the arguments of a function in a set pattern, and give an optional argument a default with `?`. [must, review]
- **NIX-22** Use `inherit (set) name;` instead of `name = set.name;`. [should, review]
- **NIX-23** Write a path that Nix must track as a path literal, such as `../../Containerfile.pg`. A string path breaks in the sandbox. [must, review]
- **NIX-24** Keep a value in one place. A login and an address live in `nix/data.nix`, and a port lives on its process. [must, review]
- **NIX-25** Declare the port of a process with `ports.<name>.allocate = <base>`, and read it with `data.port "<process>" "<name>"`. A devenv service sets its `port` option, as `services.redis` does. Don't write a port number in another place. [must, review]
- **NIX-26** Take `utils` and `data` from the module arguments. Don't import `nix/utils/` or `nix/data.nix` in a module. [must, review]
- **NIX-27** Use bash only for a script that's a list of plain commands, with no loop, condition or variable, such as `scripts.seed` or an `enterTest`. Write anything else in Nushell. [must, review]
- **NIX-28** Prefer a plain command with `env` and `cwd` to a generated wrapper script. [should, review]
- **NIX-29** Give every task and every script a `description`. [must, review]

### Nix layout, tasks and documentation

Applies to: `**/*.nix`.

#### Layout of a file

Read each file from the top to the bottom. The main part of the file is the last block.

1. The argument set, then `utils`.
2. Constants that the file uses more than once.
3. Helper functions, each after the values it uses.
4. Parts that the main block uses more than once, such as a `mkTaskChain` result.
5. The main block: `profiles.<name>`, `processes`, or `services`.

- **NIX-30** Bind a name in `let` only if the file uses it at least twice. Write a value that the file uses once where it's used. [must, review]
- **NIX-31** In a profile `module`, keep this order: `imports`, `env`, `packages`, `languages`, `scripts`, `git-hooks`, `tasks`, `enterTest`. [must, review]
- **NIX-32** In `tasks`, put the group tasks (`repo:lint`, `repo:test:unit`) last. [must, review]
- **NIX-33** Always declare processes and tasks. Don't gate them on `config.devenv.isTesting`, so `devenv up` and `devenv test` run the same stack. [must, review]
- **NIX-34** Gate only `console:open:browser`, because it would open a browser and hang `devenv test`. [must, review]
- **NIX-35** Don't give a local function the name of a helper in `nix/utils/`. Name it for what it builds, such as `mkNginx`. [must, review]
- **NIX-54** Put a helper in the file of its topic in `nix/utils/`. The file takes `{ lib, pkgs, config, utils, ... }` and returns its helpers, and `import-tree` adds it to `utils`. [must, review]

#### Processes and tasks

- **NIX-36** Make a service that stays up a `processes` entry. Make a command that runs and exits a `tasks` entry. Don't run a one-shot command as a process. [must, review]
- **NIX-37** Use `utils.mkTask` for one task and `utils.mkTaskChain` for steps in order. Add the other options of a process or task with `//`. [should, review]
- **NIX-38** Let a task that a process needs use `before`. Let a task that needs a running service use `after` and `wantedBy` with `utils.processTask "<name>"`. [must, review]
- **NIX-39** Define the `<component>:test:int` tasks in `nix/profiles/integration.nix`, so the job that runs one fetches no tool of another profile. [must, review]
- **NIX-40** Define a `<component>:test:unit` task in the unit profile of its component. [must, review]
- **NIX-41** Use `execIfModified` on a build task, on a lint or unit task, and on a suite that starts services, with the input files from `utils.mkInputs`. [must, review]
- **NIX-42** List files and globs that end in an extension, never a folder or `dir/**`. Use `utils.mkGlobs` for the files of a folder. [must, devenv]
- **NIX-43** Build a file from variables with the `files` option, and put it in the state folder with `utils.fileEntry`. Use a derivation only when the input is YAML or another format that Nix can't read. [must, review]
- **NIX-44** Give a task that reads a generated file `after = [ "devenv:files" ]`. [must, review]
- **NIX-45** Keep each devenv profile under its limit in `nix/profiles/nix.nix`. Run `nix:lint:closure` after you add a package to a profile, a task or a process. [must, devenv]

#### Documentation of a function

- **NIX-46** Write the documentation of a function as an [RFC 145](https://github.com/NixOS/rfcs/blob/master/rfcs/0145-doc-strings.md) doc comment. Start it with `/**` and put it directly before the function. [must, review]
- **NIX-47** Document functions only. Don't document a variable or a set of values. A file may have a header comment (GEN-12). [must, review]
- **NIX-48** Write the content as CommonMark. Start with one sentence that says what the function returns. [must, review]
- **NIX-49** Use these headings when they apply: `# Inputs` with one term for each argument, and `# Type` with a type signature in a code block. [must, review]
- **NIX-50** Write the type as `name :: { key :: Type, optional :: Type? } -> Result`. [must, review]
- **NIX-51** Don't add a comment that explains how code works. Name the value or split the function instead. [must, review]
- **NIX-52** Document each function of a set where it's defined, as `probes` does. [should, review]

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

#### Tests

- **NIX-53** Run `devenv --profile integration tasks run <component>:test:int` for each component that a change touches. The first run can fail when a hook formats a file. Run it again. [must, review]

## SQL

Applies to: `sync/src/sync/templates/**`, `helm/files/templates/**`, `console/migrations/**`, `helm/files/config/rbac.toml`.

Templates live in `sync/src/sync/templates/<backend>/`. Helm job templates live in `helm/files/templates/postgres/`. See [SQL Templates](sql.md).

### Code

- **SQL-01** Build SQL mappings with `Identifier()` and `Literal()`. Use parameters for runtime values. [must, review]
- **SQL-02** Never put a user value into SQL text. [must, review]
- **SQL-03** Use `ON CONFLICT ... DO UPDATE` for upserts. [should, review]
- **SQL-04** Use `timestamptz`, `jsonb` and `bigint GENERATED ALWAYS AS IDENTITY` for a column. A PostgREST contract can need `json`. [must, code]
- **SQL-05** Use one transaction per logical operation, with the `atomic()` helper. [must, review]
- **SQL-06** Use `SECURITY DEFINER` only on purpose, and grant `EXECUTE` to the required role only. [must, review]
- **SQL-07** Mark read-only functions `STABLE`. [should, review]
- **SQL-08** Reload PostgREST with `NOTIFY pgrst, 'reload schema'` through `reload_postgrest()` after the serving definitions change. Don't restart the Deployments. [must, review]
- **SQL-09** Load and delete in sets, not row by row. [must, review]

```python
✅ mapping = {"table": Identifier(schema, name), "limit": Literal(n)}
❌ query = f"SELECT * FROM {schema}.{name} LIMIT {n}"
```

### Backends

- **SQL-10** Let PostgreSQL read Parquet from object storage through `pg_duckdb`. Don't download it first. [must, review]
- **SQL-11** Let DuckDB write one `COPY (...) TO ... (FORMAT PARQUET)` for each dump task. [must, review]
- **SQL-12** Put the behavior of a synchronization source behind a `Source` adapter. Only the BigQuery adapter may use `bigquery_scan` or the BigQuery client. [must, review]
- **SQL-13** Give each `Source` adapter a Pydantic model for the non-secret `source.settings`. Reject unknown, invalid or empty settings when the registry creates the adapter. [must, review]
- **SQL-14** Keep `Source` construction free of side effects. Create external clients on first use and close them through the adapter lifecycle. [must, review]
- **SQL-15** Use a service account or Workload Identity. Don't put credentials in an image. [must, review]

### Console migrations

- **SQL-16** Keep the migrations of the `console` schema in `console/migrations/NNNNN_name.sql`, and let `goose` apply them. Add a new numbered file for each change. [must, goose]
- **SQL-17** Don't edit a migration that another install applied. `console/migrations/SHA256SUMS` has the hash of each file, and `nu scripts/rules.nu sums` rewrites it for a new migration. [must, code]
- **SQL-18** Keep `sync:lint:sql` green. It runs `sqlfluff` on the files with a limit of 88 columns, and `goose validate`. [must, sqlfluff]
- **SQL-19** Give a `SECURITY DEFINER` function a fixed `search_path`, as `data_proxy.sees` does. [must, review]
- **SQL-20** Check access with `rbac.can()` and `rbac.mine()`. Both refuse if the rule is missing. [must, review]

### Tests

- **SQL-21** Put test SQL in `sync/tests/templates/<backend>/`, with the same headers and names as the `src/` templates. Inline only one short statement, such as `SELECT set_config(...)` or `SET ROLE`. [must, review]

### Readable templates

- **SQL-22** Install a function that doesn't depend on the columns of a table once, as a static function in `routing/`. Render only identifiers and column lists in the function of a table. [must, review]
- **SQL-23** Build dynamic SQL in named variables, and call `format()` once for each statement. Don't put `format()` in an argument of `format()` when a variable can store the text. Pass values with `USING`. [should, review]
- **SQL-24** Quote a DuckDB column with `quote_column()`, a PostgreSQL identifier with `%I` and a value with `%L`. Don't quote with `replace`. [must, code]
- **SQL-25** Write a macro that takes arguments and gives text. When a macro reads or sets a variable of the function, name the variable in its header, and let the caller write the `RETURN`. [must, review]
- **SQL-26** Render an array with `sql_array` or `name_array`. Don't write a `for` loop with `loop.last` for a list. [must, review]
- **SQL-27** Name a parameter of a new function `p_<name>` and a local variable `v_<name>`. [should, review]
- **SQL-28** Keep a function under 60 lines. Split a longer function into functions with one job and a name. Four files keep an older long function on a list in `scripts/rules.nu`. [should, code]
- **SQL-29** Keep a template readable by the SQLFluff parser. Don't add `noqa: disable=PRS` to a new template. Add a file to the allowed list in `scripts/rules.nu` only with a reason line under the `noqa`. [must, code]
- **SQL-30** Keep the description of a header under 40 words. Put the detail in a Jinja comment under the header. [must, code]
- **SQL-31** List in the header `inputs` each variable that a template uses. A name that the template sets, loops over or imports isn't an input. [must, code]

```sql
✅ v_duckdb_sql := format('SELECT %s FROM view%s', v_plan.select_list, v_plan.group_by);
✅ EXECUTE format('SELECT r[''j'']::text FROM duckdb.query(%L) r', v_duckdb_sql) INTO v_result;
❌ EXECUTE format('SELECT r[''j'']::text FROM duckdb.query(%L) r', format('SELECT %s FROM view%s', a, b)) INTO v_result;
```

### Permissions

Add a permission in these steps:

1. Add it under `[permissions.<object>]` in `helm/files/config/rbac.toml`, and give it to a role.
2. Call `rbac.can('<name>')` in the SQL that needs it.
3. Run `devenv --profile repo tasks run chart:lint:toml`. It checks the file with `taplo` and the JSON schema.
4. Add a role test in `sync/tests/unit/test_templates_roles.py`.
5. Update the permission tables in `docs/security/permissions.md`.

The `configure-db` job runs `verify-rbac` and fails if the database differs from the file.

## TypeScript

Applies to: `console/ui/src/**/*.ts`, `console/ui/tests/**`.

The console is a SvelteKit app in `console/ui/`. The sources of the rules are the [Svelte best practices](https://svelte.dev/docs/svelte/best-practices), the [typescript-eslint configurations](https://typescript-eslint.io/users/configs) and the [Google TypeScript guide](https://google.github.io/styleguide/tsguide.html). ESLint and `svelte-check` enforce the rules, so a rule that a tool can't check is the exception.

### Architecture

- **TS-01** Never call PostgREST from the browser. A `load` function or a form action in `console/ui/src/routes` calls it through `lib/server/postgrest.ts`, with the token of the session. [must, review]
- **TS-02** Keep the token and every secret in `lib/server/`. [must, review]
- **TS-03** Keep the logic that needs a test in a module with no import of `$app` or `$env`, so that Vitest runs it. Pass the settings and `fetch` as arguments. [must, review]
- **TS-04** Map a status to a message in one place (`postgrest.ts` and `messages.ts`), so that a page never writes its own text for a refusal. [must, review]

### Code

- **TS-05** Use `strict` and `noUncheckedIndexedAccess`. Lint with the typescript-eslint `strictTypeChecked` and `stylisticTypeChecked` configurations. [must, eslint]
- **TS-06** Don't use `any`, the non-null `!`, or a type assertion. Only `as const` is allowed. [must, eslint]
- **TS-07** Type a value where it enters the program, with a valibot schema. Examples are a PostgREST answer, a response of the identity provider, a cookie, a request body, a form field and a message from a frame. [must, review]
- **TS-08** Derive the type with `v.InferOutput`. `lib/types.ts` has the schemas of the rows, so a column that a view loses fails at the first read, with the field in the message. [should, review]
- **TS-09** Use `type` for object types. Use `interface` only where a library needs declaration merging, such as `App.Locals` and `Window`. [must, eslint]
- **TS-10** Use named exports. A `.svelte` file and a SvelteKit route file are the only exceptions. [must, eslint]
- **TS-11** Return a `Result` for a failure that the caller can handle, such as an answer of PostgREST. Throw for an input that the program can't run without. [must, review]
- **TS-12** Write no comment inside a function. Write a one-line `/** */` summary above an exported function only when its name doesn't say enough. [must, code]
- **TS-13** Give a lint disable comment a reason. A marker that a tool reads, such as `// pragma: allowlist secret`, is allowed. [must, review]

```ts
✅ const row = v.parse(RowSchema, data);
❌ const row = data as Row;
```

### Tooling

`devenv --profile console tasks run console:lint:ui` runs `svelte-check`, ESLint (`eslint-plugin-svelte` recommended and typescript-eslint) and `prettier --check`, for `console/ui/` and `console/notebooks/`. Run `pnpm format` in the folder to format. prek runs the hooks `prettier` and `eslint` at commit time. They use the binaries of `console/ui/node_modules`, so run `pnpm install` once.

### Tests

- **TS-14** Test a unit with Vitest, in a `*.test.ts` file next to the module. Use a table of cases with a name for each case. [must, review]
- **TS-15** Write a browser test as a `playwright-bdd` scenario that a user could read. Let a step do one thing. [must, review]
- **TS-16** Keep scenarios independent, so they can run in parallel. Let a scenario create the data that it needs, with a name that no other scenario uses. [must, review]
- **TS-17** Put a scenario that changes data that others read in `tests/features/shared/`. [must, review]
- **TS-18** Tag a scenario that adds a schema or a table `@new-schema`, write each name with the step parameter `{name}`, and sign in as `web_root`. The parameter adds a random suffix. [must, review]
- **TS-19** Wait for "Saved." with `saved()`, or wait for the row that the action creates. A toast that an earlier step left on screen matches a later wait for the same text. [must, review]
- **TS-20** Wait for a condition, never for a time. Use a web-first assertion (`expect(...).toBeVisible()`), `expect.poll`, or the `hydrated` helper. [must, review]
- **TS-21** Wait a fixed time only to check that something doesn't happen. [must, review]
- **TS-22** Write no test for the notebooks (`console/notebooks/`): no unit test, no browser test, and no scenario that opens the notebook. [must, review]
- **TS-23** Hand state from one step to the next through the typed `world` fixture in `console/ui/tests/fixtures.ts`. Don't attach it to `page`. [must, review]

## Svelte

Applies to: `console/ui/src/**/*.svelte`.

### Runes and components

- **SV-01** Use runes only. Use `$state` for a value that changes what the page shows, and a plain variable for the rest. [must, eslint]
- **SV-02** Compute a value with `$derived`. Don't copy a prop or `data` into `$state` in an `$effect`: derive from the prop, and keep only the changed part in `$state`. [must, eslint]
- **SV-03** Use `$effect` only for a system outside Svelte, such as a timer or a listener, and return its cleanup. Use `{@attach}` to sync a DOM element, as `Modal.svelte` does. [must, review]
- **SV-04** Treat props as values that change. Derive a value that depends on a prop. [must, svelte-check]
- **SV-05** Use a keyed `{#each}` with a key that's unique to the item. Never use the index. [must, eslint]
- **SV-06** Write events as attributes (`onclick`). Don't use `on:`. [must, eslint]
- **SV-07** Pass every internal `href` through `resolve()` from `$app/paths`. For a query string, use a `GET` form. [must, eslint]
- **SV-08** Write each repeated piece of markup once, as a component in `lib/components/`. A component that belongs to one page stays in the folder of that route. [must, review]
- **SV-09** Keep a `.svelte` file to at most 80 lines of script and 150 lines of template. [must, review]

```svelte
✅ {#each rows as row (row.id)}
❌ {#each rows as row, i (i)}
```

### Forms

- **SV-10** Use `use:enhance` on every POST form, except sign-out. A GET form (a filter or a pager) doesn't use it. [must, review]
- **SV-11** Give every input a visible label (`Field` and `Check`). An input in a table row uses `aria-label`, because its column gives the visible name. [must, svelte-check]
- **SV-12** Give every POST form a `Submission` from `lib/enhance.svelte.ts` and a `SubmitButton`, so the form shows its pending state and sends one request. [must, review]
- **SV-13** Return `{ toast }` from a form action when it works. A dialog shows a refusal inside itself (`inline`), and the page shows the refusal of any other form. [must, review]
- **SV-14** Ask for a confirmation before a delete (`FormDialog` with `danger`), and hide the row until the answer comes (`begin` of the `Submission`). [must, review]
- **SV-15** Use `FilterForm` for the filters of a list and `Time` for a time. Don't write a timer in a page. [must, review]
- **SV-16** Reload live data with `poll(key)` and `depends(key)` from `lib/poll.svelte.ts`. [must, review]
- **SV-17** Style with Tailwind and DaisyUI classes. Import icons by name from `@lucide/svelte`. [must, review]

## Notebooks

Applies to: `console/notebooks/**`.

- **NB-01** Never store a token, a cookie or a secret in a notebook. Get the data from the grant that the console sends, and read only the links in that grant. [must, review]
- **NB-02** Give every variable of a marimo cell a name that no other cell uses. A duplicate stops the whole notebook. [must, review]
- **NB-03** Check the origin and the source of every message in `console/notebooks/src/bridge.ts`, and send a message to one exact origin, never to `*`. [must, review]

See [TypeScript](#typescript) for the rule about tests (TS-22).

## Documentation

### Writing, pages and Vale

Applies to: `docs/**`, `README.md`, `AGENTS.md`, `STYLEGUIDE.md`.

#### Writing

- **DOC-01** Use Simplified Technical English: short sentences, active voice, one idea per sentence. [must, vale]
- **DOC-02** Keep a sentence at 25 words or fewer and a paragraph at 6 sentences or fewer. `docs:lint:style` checks the sentences. [must, devenv]
- **DOC-03** Expand an acronym at its first use on a page, and define each project term once in `docs/glossary.md`. Add a row to its Tools table for each new tool. [must, vale]
- **DOC-04** Use the fixed terms `access policy` (the table), `row policy` (the PostgreSQL rule) and `sync run`. [must, review]
- **DOC-05** Write `contains`, `stores` or `has`, and not `holds`. Write `immediately`, and not `at once`. Vale enforces the word swaps and the ban on semicolons. [must, vale]
- **DOC-06** Use contractions in Markdown. The Python and Nushell checks turn that rule off, because messages keep their full forms. [must, vale]
- **DOC-07** Rephrase a sentence that Vale flags, such as the acronym for a web address, some adverbs and acronyms in headings. Don't ignore the message. [must, vale]
- **DOC-08** Use generic examples unless a real value is required. [should, review]
- **DOC-09** Say "the source" for the product, and name BigQuery only on a page about the BigQuery adapter or for a value that's specific to it. [must, review]
- **DOC-10** Verify claims against the source, the Helm templates, the tests, or a command that you ran. [must, review]

#### Pages and links

- **DOC-11** Write the documents in `docs/`. The home page is `docs/index.md`, and each folder has an `index.md` that lists its pages. [must, review]
- **DOC-12** Name a file with one word, and use two words only if one word is unclear. [should, review]
- **DOC-13** Give each topic one page, and link to it from the other pages. Keep a page near 800 words. `docs:lint:style` fails above 1000, unless the page is on a list in `scripts/docs.nu`. [must, devenv]
- **DOC-14** Link to another page with a relative Markdown link. Write the path to the `.md` file and the slug of the heading (lower case, spaces as hyphens), as in `[Roles](../security/roles.md#roles)`. Link to a folder with its `index.md`. [must, devenv]
- **DOC-15** Run `docs:lint:links` after you move a page or a heading. Marksman completes and follows the links in the editor, but it doesn't flag a broken one. [must, devenv]
- **DOC-16** End every page with the navigation block (Previous, Next, Index and Home) that `docs:build:nav` writes. Don't edit the block by hand. [must, devenv]
- **DOC-17** Put the page order in `docs/.nav.json`. Add a new page to that file, and link it from its parent page. [must, devenv]
- **DOC-18** Expect `docs:lint:nav` to fail on a missing page, a page without a link from its parent, or an old navigation block. [must, devenv]
- **DOC-19** Don't write by hand a table that a file already defines. `docs/security/roles.md` has a role table that `docs:build:roles` writes from `rbac.toml`, and `docs:lint:roles` checks it. [must, devenv]
- **DOC-20** Run `nix:lint:cmd` after you rename a task or a profile. It fails on a `devenv` command in a page that names a profile or a task that doesn't exist. [must, devenv]

#### Vale

- **DOC-21** Let the `vale` git hook check the staged `*.md`, `*.py` and `*.nu` files at commit time. Run `vale <file>` in the `repo` shell to check a file earlier. [must, vale]
- **DOC-22** Let `DataProxy.Terms` fix the casing of product names: PostgreSQL, PostgREST, SeaweedFS, DuckDB and BigQuery. [must, vale]
- **DOC-23** Add a word to `.vale/styles/config/vocabularies/data-proxy/accept.txt` at the end of the file only. [must, vale]
- **DOC-35** Write a style area as a list of rules in [the rule format](#the-rule-format). Keep a rule to 40 words and an area to 2000 words. [must, devenv]

### Diagrams

Applies to: `docs/diagrams/**`, `docs/**/*.svg`.

#### Where a diagram lives

- **DOC-24** Give a diagram one owner page, and embed its SVG there once. Other pages link to the owner. `docs:lint:diag` fails for an SVG that no page embeds or that two pages embed. [must, devenv]
- **DOC-25** Give every diagram a short name, and write it as the alt text of the image. Don't add a caption line under the image. [must, review]
- **DOC-26** Draw one context diagram on `docs/index.md` and one overview diagram of the whole system on `docs/architecture/index.md`. [must, review]
- **DOC-27** Put a diagram in `docs/diagrams/<section>/`, where the section is the folder of its owner page. [must, review]
- **DOC-28** Pick the diagram type from the question that the page answers, as the next table shows. Use a table when a diagram adds nothing. [must, review]
- **DOC-36** Start a page with one or two sentences that say what the page is for. Show the diagram or the steps next, and put reference tables last. [should, review]
- **DOC-37** Keep a diagram to about 12 boxes. Split a larger one, or draw the detail on the page of the part. [should, review]
- **DOC-38** Write a box label as a noun phrase and an edge label as a verb phrase. [should, review]

| Question | D2 type | Example |
| --- | --- | --- |
| What happens in order, between parties? | `sequence_diagram` | `serving-sequence` |
| Which path does a request take, and where does it branch? | Flowchart with `diamond` shapes | `authorization` |
| Which states exist, and how does a thing move between them? | Flowchart with labeled edges | `run-states` |
| What runs where? | Nested containers with `cylinder` for storage | `topology` |
| Which parts exist and how do they relate? | Nested containers and edges | `overview`, `context` |
| What's in each folder or set? | `grid` | `code-map` |
| Which tables and columns exist? | `sql_table` | `application` |

#### How to draw it

- **DOC-29** Write a diagram in D2 in `docs/diagrams/<name>.d2` or in a folder inside it, such as `docs/diagrams/schemas/`. Don't write a diagram in another language. [must, review]
- **DOC-30** Let `db.d2` import the other files of its folder. Edit a table in its own file and not in `db.d2`. [must, review]
- **DOC-31** Render a diagram with `devenv --profile default tasks run docs:build:diag`. Commit the `.d2` and the `.svg`, and embed the SVG with an image link and an alt text. [must, devenv]
- **DOC-32** Know that `docs:lint:diag` (profile `default`) fails when an SVG is out of date or embedded wrongly, and that CI doesn't run it. [should, devenv]
- **DOC-33** Make every diagram render in a light and a dark theme. [must, review]
- **DOC-34** Draw a database diagram with `sql_table` shapes, and take the columns from the SQL files. [must, review]

## Git

Applies to: every commit.

### Commits

- **GIT-01** Use Conventional Commits: `type(scope)!: imperative summary`, lowercase, no trailing period. The scope is optional. [must, code]
- **GIT-02** Use only `feat` (user-visible capability), `fix` (user-visible defect), `refactor`, `test`, `docs`, `revert` and `chore`. Use `chore` for CI, tooling, dependencies and maintenance. [must, code]
- **GIT-03** Don't use `perf` or `ci`. Classify release-worthy performance work as `feat` or `fix`, and classify CI work as `chore`. [must, code]
- **GIT-04** Use `chore` for a change of tooling, even when it touches the chart folder. A `fix` or a `feat` releases the chart when `helm/` changed since the last release. [must, cog]
- **GIT-05** Make one logical change per commit. Squash fixups before merge. [should, review]

```text
✅ chore(nix): slim the devenv profiles
❌ ci: slim the devenv profiles
```

### Releases

- **GIT-06** Expect these release levels: a type with `!` bumps major, `feat:` bumps minor, `fix:` bumps patch, and all other types create no release. `cog` applies the rules, and `cog.toml` has its settings. [must, cog]
- **GIT-07** Use `!` only with `feat` or `fix` release commits. Explain the migration in the body. Don't use a `BREAKING CHANGE` footer. [must, cog]
- **GIT-08** Don't edit `CHANGELOG.md` by hand. The release step writes it from the commits, and it lists only breaking changes, features and fixes. [must, cog]

```text
feat(sync)!: require DBOS workflow recovery

Sync recovery now requires the DBOS worker and workflow state in PostgreSQL.

Migration:
- Deploy the DBOS worker before you enable scheduled syncs.
- Verify the DBOS system database is reachable.
```

### Hooks

- **GIT-09** Know that prek runs the hooks at commit time. If a hook rewrites a file, stage the file again and commit again. [must, review]
- **GIT-10** Know that `ripsecrets` blocks a line that looks like a secret. Add a whole file to `.secretsignore`, or mark one line with `pragma: allowlist secret`, as `helm/tests/ducklake_catalog_test.yaml` does. [must, ripsecrets]

<!-- nav:start -->
---

[Previous: Query tab scope](console/scope.md) | [Next: Development](dev/index.md) | [Home](index.md)
<!-- nav:end -->
