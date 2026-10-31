# Proxy rocks

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

- `proxy:lint:lua` runs luacheck (through `lx lint`) and the LuaLS type check with every rule that works for this code.
- Luacheck uses the `ngx_lua` globals, a 120-character line limit, and a limit of 10 branches per function.
- LuaLS has every diagnostic group on, down to the hint level. Four checks are off:
  - `codestyle-check`, because stylua formats the code
  - `spell-check`, because it flags names such as `PostgREST`
  - `name-style-check` and `no-unknown`, because they reject conventions that the code uses on purpose

**Tools**

- Lux installs `busted` for `lx test`, and `stylua` and `luacheck` for `lx fmt` and `lx lint`. Nix therefore provides only LuaJIT, the language server for editors, and `lx`.
- `Containerfile.proxy` runs `lx build --only-deps` against the same lockfile. The image gets the runtime rocks and no test rocks.

```bash
devenv --profile proxy tasks run proxy:install:rocks
```

The task installs the rocks into the ignored `proxy/.lux/` tree and fails when `lux.lock` changes. Change a version with `lx add` or `lx update` in `proxy/`, then commit the lockfile. Add a test library with `lx add --test`. `proxy:lint:lua` runs `lx fmt`, `lx lint`, and the LuaLS type check, and fails when formatting changed a file. OpenResty bundles `resty.redis`, `resty.string`, `resty.lock`, and `cjson`, so only `lua-resty-http` is a runtime rock. Run the checks with `devenv --profile proxy tasks run proxy:lint:lua proxy:tests:int`.

<!-- nav:start -->
---

[Previous: Checks](checks.md) | [Next: Frontend](frontend.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
