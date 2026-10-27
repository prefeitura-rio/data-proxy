---@meta

--- A schema: a name, the fields of the data, and the checker that validates and converts a value.
---@class Schema<T>: pl.Class
---@overload fun(name: string, fields: table<string, tableshape.Type<any>>): Schema<any>
---@field name string Name of the schema, the same as its field in the registry.
---@field fields table<string, tableshape.Type<any>> The type of each field, by name.
---@field checker tableshape.Type<T> The checker that validates and converts a value.
