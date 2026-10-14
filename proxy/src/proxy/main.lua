-- Entry points that nginx calls: init once, start in each worker, handle and reload for requests.
-- The only module with state.

local cjson = require("cjson.safe")
local file = require("pl.file") --[[@as pl.file]]
local handler = require("proxy.handler")
local settings = require("proxy.settings")
local sync = require("proxy.sync")

--- The config from the environment. init loads it.
---@type Config|nil
local config

--- The dictionary that every worker shares. It holds the text of sync.json and its version. init sets it.
---@type SettingsStore|nil
local store

--- The table settings that this worker parsed last, and the version that they came from.
---@type SettingsCache
local cache = {}

--- The entry points that nginx calls.
---@class Main
local main = {}

--- Load the config and the first sync.json. The first sync.json comes from the file if it exists. The
--- console replaces it later. If a variable is not valid, or the file is not valid or is missing and no
--- console address is set, the function raises an error, and nginx does not start. Call from init_by_lua_block.
---@param path string Path of sync.json.
---@param getenv (fun(name: string): string|nil)|nil Gives the text of a variable. Default: os.getenv.
---@return nil
function main.init(path, getenv)
	config = require("proxy.config").load(getenv or os.getenv)
	store = ngx.shared.proxy_settings --[[@as SettingsStore|nil]]
	if store == nil then error("Declare lua_shared_dict proxy_settings") end

	local text, read_error = file.read(path)
	if text ~= nil then
		local parsed, reason = sync.parse(text)
		if parsed == nil then error(reason) end
		settings.replace(store, text, "file")
	elseif config.config_url == nil then
		error(read_error)
	end
end

--- Start to check the console for a new sync.json, in one worker only. Call from init_worker_by_lua_block.
---@return nil
function main.start()
	if config == nil or store == nil then error("main.init was not called") end
	if config.config_url == nil or ngx.worker.id() ~= 0 then return end

	local url = config.config_url --[[@as string]]
	local interval = config.config_refresh_seconds

	local function check(premature)
		if premature then return end
		local _, reason = settings.reload(store --[[@as SettingsStore]], url, settings.fetch)
		if reason then ngx.log(ngx.ERR, "Could not load sync.json from the console: ", reason) end
		ngx.timer.at(interval, check)
	end

	ngx.timer.at(0, check)
end

--- Serve one request. Call from content_by_lua_block.
---@return nil
function main.handle()
	if config == nil or store == nil then error("main.init was not called") end
	handler.handle(settings.current(store, cache), config)
end

--- Load sync.json from the console now, and answer with the result. The console calls this after it
--- changes the config. Call from content_by_lua_block of the admin server.
---@return nil
function main.reload()
	if config == nil or store == nil then error("main.init was not called") end

	ngx.header["Content-Type"] = "application/json"
	if ngx.req.get_method() ~= "POST" then
		ngx.header["Allow"] = "POST"
		ngx.status = ngx.HTTP_NOT_ALLOWED
		return ngx.print(cjson.encode({ error = "Use POST" }))
	end
	if config.config_url == nil then
		ngx.status = ngx.HTTP_CONFLICT
		return ngx.print(cjson.encode({ error = "PROXY_CONFIG_URL is not set" }))
	end

	local result, reason = settings.reload(store, config.config_url, settings.fetch)
	if result == nil then
		ngx.log(ngx.ERR, "Could not reload sync.json: ", reason)
		ngx.status = ngx.HTTP_BAD_GATEWAY
		return ngx.print(cjson.encode({ error = reason }))
	end
	ngx.print(cjson.encode(result))
end

return main
