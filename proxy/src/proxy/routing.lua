-- Choose the PostgREST service for a request, and the one for the retry.

local constants = require("proxy.constants")

--- Functions that choose the PostgREST service for a request.
---@class Routing
local routing = {}

--- Select the endpoints for one request.
---
--- ```lua
--- local config = { ha_mode = true, postgrest_read = "http://r", postgrest_write = "http://w" }
--- routing.select("GET", config)   --> { postgrest = "http://r", retry_postgrest = "http://w" }
--- routing.select("POST", config)  --> { postgrest = "http://w", retry_postgrest = nil }
--- ```
---@param method string HTTP method, for example GET.
---@param config Config The config. It uses ha_mode, postgrest_read and postgrest_write are used.
---@return Route route
function routing.select(method, config)
	local mode = config.ha_mode and "ha" or "single"
	local request_kind = constants.http.read_methods[method] and "read" or "write"
	local rule = constants.routing.rules[mode][request_kind]

	local postgrest = config["postgrest_" .. rule.primary] or ""
	local retry_postgrest = rule.retry and config["postgrest_" .. rule.retry] or nil
	return { postgrest = postgrest, retry_postgrest = retry_postgrest }
end

return routing
