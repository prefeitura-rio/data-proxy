---@meta

--- The result of reading one query parameter.
---@class ParameterResult
---@field node FilterNode|nil The filter node. nil if the parameter is not a supported filter.
---@field readable boolean false if a percent escape in the key or value is not valid.
