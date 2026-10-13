-- A schema: a name, the fields of the data, and the checker that validates and converts a value.

local class = require("pl.class") --[[@as pl.ClassModule]]
local types = require("tableshape").types

--- A schema: a name, the fields of the data, and the checker for it.
---@class Schema<T>
local Schema = class()

--- Make a schema. The checker is open, because every schema describes data from outside.
---@param name string Name of the schema.
---@param fields table<string, tableshape.Type<any>> The type of each field.
function Schema:_init(name, fields)
	self.name = name
	self.fields = fields
	self.checker = types.shape(fields, { open = true })
end

--- Check a value with the checker, and convert it if the checker converts.
--- A valid table that the checker does not change comes back as the same table.
---
--- ```lua
--- Schema("cached_entry", fields):validate({ body = "[1]" })  --> { body = "[1]" }, nil
--- Schema("cached_entry", fields):validate({})                --> nil, 'field "body"'
--- ```
---@param value any The value to check, for example the result of cjson.decode.
---@return T|nil value The validated value. nil if the value is not valid.
---@return string|nil reason Why the value is not valid. It names the field.
function Schema:validate(value) return self.checker:transform(value) end

return Schema
