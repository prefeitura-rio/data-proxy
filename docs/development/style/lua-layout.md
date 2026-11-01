# Lua layout, tests and checks

Applies to: `proxy/src/proxy/**`, `proxy/tests/**`, `proxy/lux.toml`.

The proxy is OpenResty and Lua code in `proxy/src/proxy`. It runs in the pinned OpenResty image.

## Layout and dependencies

- **LUA-01** Keep one named module for each concern: `cache`, `config`, `constants`, `handler`, `limiter`, `log`, `main`, `parser`, `postgrest`, `redis`, `regex`, `request`, `response`, `schemas`, `settings`, `sync` and `utils`. [must, review]
- **LUA-02** Write a module in this order: imports, local variables, local functions, classes, and the returned object (the module table, the functions on it, and `return`). [must, review]
- **LUA-03** Put a local variable that needs a local function right after that function. [must, review]
- **LUA-04** Declare the module table with the other local variables when nothing else needs it later. [should, review]
- **LUA-05** Name modules `proxy.<name>`. Let `nginx.conf` load only `proxy.main`. [must, review]
- **LUA-06** Return the module table directly. Don't add re-export modules. [must, review]
- **LUA-07** Read the `PROXY_*` variables only through the table that `config.load` (`proxy/src/proxy/config.lua`) returns. [must, review]
- **LUA-08** Let only `main` load the config, once at start-up in `main.init`, and pass it down as a parameter named `config`. [must, review]
- **LUA-09** Don't call `os.getenv` or read `ngx.var` for a setting in another module. [must, review]
- **LUA-10** Declare each setting, with its type, default and limits, in `proxy/src/proxy/schemas/definitions/config.lua`. List its `PROXY_` variable in `helm/files/config/nginx.conf` with an `env` directive. [must, review]
- **LUA-11** Use the module `settings` only for the `sync.json` table that every worker shares and that the console replaces. [must, review]
- **LUA-12** Define every named constant in `constants.lua`, in a group with lower case names, such as `constants.redis.connect_timeout_ms`. Other modules define no constants. [must, review]
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

## Tests

- **LUA-25** Write one test file for each module, `proxy/tests/unit/<module>_spec.lua`, and keep `proxy/tests/unit` flat. `config`, `constants`, `regex`, `main`, `handler` and `log` have no test file. [must, review]
- **LUA-26** Write table-driven Busted specs, with one `describe` for each function and `it(case.name)` for each case. [must, review]
- **LUA-27** Give each case a name that states the behavior, for example `retries a 503 on the retry service`. [must, review]
- **LUA-28** Keep fakes and fixtures in `proxy/tests/unit/helpers.lua` and `proxy/tests/unit/fixtures`. Make a fake follow the shape of the real API, such as the 32 raw bytes that `resty.sha256` gives. [must, review]
- **LUA-29** Test pure functions only. A function with an effect, such as a request, a socket or an nginx phase, belongs to the Hurl suite in `proxy/tests/integration/` or to k6. [must, review]
- **LUA-30** Compare a rewrite with the old code on random inputs before you delete the old code. [should, review]

## Checks

- **LUA-31** Run `devenv --profile proxy tasks run proxy:lint:lua proxy:test:unit` before review. [must, devenv]
- **LUA-32** Keep `proxy:lint:lua` green: `luacheck` (line length 120, complexity 10) with `lx lint`, and the LuaLS type check at the Hint level with no problems. [must, luacheck+luals]
- **LUA-33** Let the git hook run `lx fmt` (StyLua), and use tabs. [must, stylua]
- **LUA-34** Restore `proxy/lux.lock` with `git checkout -- proxy/lux.lock` after a sync that only reorders it. [should, review]
- **LUA-35** Start the editor inside the devenv shell, so that LuaLS can read `LPEG_TYPES`. [should, review]

<!-- nav:start -->
---

[Previous: Python](python.md) | [Next: Lua code](lua-code.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
