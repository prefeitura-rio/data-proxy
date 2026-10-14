-- Handle one request: read it, route it, answer from the cache or PostgREST, and send the answer.

local Redis = require("proxy.redis")
local cache = require("proxy.cache")
local constants = require("proxy.constants")
local postgrest = require("proxy.postgrest")
local request = require("proxy.request")
local response = require("proxy.response")
local routing = require("proxy.routing")

--- The function that handles one request.
---@class Handler
local handler = {}

--- Read the request body. nginx keeps a body that is larger than client_body_buffer_size in a temporary file.
---@return string body The body, or an empty text if the request has none.
local function read_body()
	ngx.req.read_body()
	local data = ngx.req.get_body_data()
	if data then return data end

	local path = ngx.req.get_body_file()
	local file = path and io.open(path, "rb")
	if not file then return "" end

	local content = file:read("*a")
	file:close()
	return content or ""
end

--- Copy the current nginx request into an NgxRequest.
---@param config Config
---@return NgxRequest
local function read_nginx_request(config)
	return {
		method = ngx.req.get_method(),
		uri = ngx.var.uri,
		args = ngx.var.args or "",
		body = read_body(),
		generation = 0,
		headers_in = ngx.req.get_headers() --[[@as table<string,string|nil>]],
		headers_out = ngx.header --[[@as table<string,string>]],
		cache_ttl = config.cache_ttl,
		max_body = config.max_body,
		retries = config.postgrest_retries,
		backoff_ms = config.postgrest_backoff_ms,
		respond = function(_, status, body)
			ngx.status = status
			ngx.print(body)
		end,
	}
end

--- Handle the current request. Call this from content_by_lua_block.
--- Each step takes values and gives a new value. The steps that have effects are the last two.
---@param loaded_config SyncConfig The table settings that main.init loaded.
---@param config Config The config from the environment.
---@return nil
function handler.handle(loaded_config, config)
	local store = Redis(config)
	local ngx_request = read_nginx_request(config)
	if constants.http.read_methods[ngx_request.method] then ngx_request.generation = store:generation() end
	local route = routing.select(ngx_request.method, config)
	local context = request.make_context(ngx_request, route, { config = loaded_config, now = ngx.now() })
	local cache_key = request.cache_key(context)
	local result = cache.fetch(context, cache_key, function() return postgrest.query(context) end, store)
	if cache.changes_policy(context, result.response) then store:bump_generation() end

	response.send(ngx_request, context, result)
end

return handler
