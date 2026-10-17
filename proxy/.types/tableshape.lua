---@meta tableshape

--- Types of the tableshape rock, for the language server. The rock has no type definitions.
--- Only the part that the proxy uses is described.

--- A type: it checks a value and can convert it. T is the type of the result.
---@class tableshape.Type<T>
---@operator add(tableshape.Type<any>): tableshape.Type<any>
---@operator mul(tableshape.Type<any>): tableshape.Type<any>
---@operator div(any): tableshape.Type<any>
---@field shape table<string, tableshape.Type<any>>|nil The field types of a shape from types.shape.
local Type = {}

--- Check a value, and convert it if the type has a conversion. Gives the same table if nothing changed.
---@param value any
---@return T|nil value nil if the value is not valid.
---@return string|nil reason Why the value is not valid. It names the field.
function Type:transform(value) end

--- Check a value without a result.
---@param value any
---@return boolean|nil valid
---@return string|nil reason
function Type:check_value(value) end

--- Accept nil too.
---@return tableshape.Type<T|nil>
function Type:is_optional() end

--- Change the description in the reason of a failure.
---@param text string|fun(self: tableshape.Type<any>): string
---@return tableshape.Type<T>
function Type:describe(text) end

--- Limit the length of a text or an array.
---@param min number
---@param max number Use math.huge for no limit.
---@return tableshape.Type<T>
function Type:length(min, max) end

---@class tableshape.ShapeOptions
---@field open boolean|nil True to accept fields that are not in the shape, and keep them.
---@field check_all boolean|nil True to collect every error.

---@class tableshape.Types
---@field string tableshape.Type<string>
---@field number tableshape.Type<number>
---@field boolean tableshape.Type<boolean>
---@field table tableshape.Type<table>
---@field null tableshape.Type<nil>
---@field any tableshape.Type<any>
---@field integer tableshape.Type<integer> A number that has only digits when it is printed: a whole number, not negative.
local types = {}

--- A table with the given fields.
---@generic T
---@param fields table<string, tableshape.Type<any>|any>
---@param options tableshape.ShapeOptions|nil
---@return tableshape.Type<T>
function types.shape(fields, options) end

--- The first of the options that matches the value. An option is a type or a literal value.
---@param options (tableshape.Type<any>|string|number|boolean)[]
---@return tableshape.Type<any>
function types.one_of(options) end

--- A type that is built when the value is checked. It lets a type refer to itself.
---@param fn fun(): tableshape.Type<any>
---@return tableshape.Type<any>
function types.proxy(fn) end

---@class tableshape.ArrayOptions
---@field keep_nils boolean|nil True to keep the items that a transform turned into nil.
---@field length tableshape.Type<any>|nil Checker for the length of the array.

--- A table with keys and values of the given types.
---@param key tableshape.Type<any>
---@param value tableshape.Type<any>
---@return tableshape.Type<table>
function types.map_of(key, value) end

--- An array of items of the given type.
---@param item tableshape.Type<any>
---@param options tableshape.ArrayOptions|nil
---@return tableshape.Type<table>
function types.array_of(item, options) end

--- A text that matches a Lua pattern.
---@param pattern string
---@return tableshape.Type<string>
function types.pattern(pattern) end

--- A number from min to max.
---@param min number
---@param max number
---@return tableshape.Type<number>
function types.range(min, max) end

---@class tableshape
---@field types tableshape.Types
---@field VERSION string
---@field is_type fun(value: any): boolean True if the value is a type, and not a literal value.
local tableshape = {}

return tableshape
