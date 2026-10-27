---@meta

--- The options of parser.parse.
---@class ParseOptions
---@field limit boolean|nil true if the caller allows the limit and the order in the document.

--- The result of reading one query parameter.
---@class ParameterResult
---@field node FilterNode|nil The filter node. nil if the parameter is not a supported filter.
---@field readable boolean false if a percent escape in the key or value is not valid.
---@field reserved string|nil The reserved key (select, order, limit, or offset) that the parameter has.
---@field value string|nil The decoded value of a reserved parameter.
