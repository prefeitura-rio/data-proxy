# Lua code

Applies to: `proxy/src/proxy/**/*.lua`.

## Design

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

## Errors and names

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

## Penlight

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

<!-- nav:start -->
---

[Previous: Lua layout, tests and checks](lua-layout.md) | [Next: Lua types and comments](lua-types.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
