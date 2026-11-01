# Lua types and comments

Applies to: `proxy/src/proxy/**/*.lua`, `proxy/src/proxy/**/*.d.lua`, `proxy/.types/**`.

## Types

- **LUA-58** Describe every function with `---@param` and `---@return`, and every shared table with `---@class` and `---@field`. [must, luals]
- **LUA-59** Put the types of a module in `<module>.d.lua`, next to `<module>.lua`. Start the file with `---@meta`, and write no other file comment. [must, review]
- **LUA-60** Put a type in the file of the module that creates it. Don't write types in `<module>.lua`, except the `---@class` of the module table and the class of an object that the module implements with methods. [must, review]
- **LUA-61** Keep a class next to its `local` table, and declare no `---@field` for functions that the file defines. LuaLS reports `inject-field` for a function that a file adds to a class that another file declares. [must, luals]
- **LUA-62** Don't use `table` or `any` if a named type exists. [must, review]
- **LUA-63** Use `--[[@as Type]]` for a cast, and only at an external boundary. [must, review]
- **LUA-64** Don't use `---@diagnostic disable` in `proxy/src`. Fix the cause. A test file can disable `missing-fields`, because a case sets only the fields that it uses. [must, luals]
- **LUA-65** Add the type definitions of a library that has none to `proxy/.types/`. Load LPeg types from `LPEG_TYPES` and Penlight types from `PENLIGHT_TYPES`. [must, review]

## Schemas

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

## Comments

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

<!-- nav:start -->
---

[Previous: Lua code](lua-code.md) | [Next: Nushell](nushell.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
