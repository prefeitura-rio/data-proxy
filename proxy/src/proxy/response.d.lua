---@meta

--- The answer that response.send gives to the client.
---@class ResponsePlan
---@field status integer HTTP status of the answer.
---@field body string Body of the answer.
---@field headers table<string,string> Headers to copy to the client. Do not change this table.
---@field x_cache string|nil Value of the X-Cache header: HIT or MISS. nil if the answer has none.
---@field default_content_type string|nil Content-Type to use if the headers have none. nil if there is no default.
---@field log_fields table<string,string|number> Fields for the request log line.
