-- Entry points that nginx calls: init once, start in each worker, handle and reload for requests.
-- The only module with state.

local file = require("pl.file") --[[@as pl.file]]
local handler = require("proxy.handler")
local path_lib = require("pl.path") --[[@as pl.path]]
local settings = require("proxy.settings")
local sync = require("proxy.sync")

--- The config from the environment. init loads it.
---@type Config|nil
local config

--- Where sync.json is. init sets it.
---@type string|nil
local sync_path

--- The dictionary that every worker shares. It holds the text of sync.json and its version. init sets it.
---@type SettingsStore|nil
local store

--- The table settings that this worker parsed last, and the version that they came from.
---@type SettingsCache
local cache = {}

--- The entry points that nginx calls.
---@class Main
local main = {}

--- Give the text of a file, nil for a file that does not exist, or nil and a reason.
---@param path string
---@return string|nil text
---@return string|nil reason
local function read_file(path)
	if not path_lib.exists(path) then return nil end
	return file.read(path)
end

--- Load the config and the first sync.json. Without a file the proxy starts with its defaults and takes
--- the file when it appears. If a variable is not valid, or the file exists and is not valid, the
--- function raises an error, and nginx does not start. Call from init_by_lua_block.
---@param path string Path of sync.json.
---@param getenv (fun(name: string): string|nil)|nil Gives the text of a variable. Default: os.getenv.
---@return nil
function main.init(path, getenv)
	config = require("proxy.config").load(getenv or os.getenv)
	store = ngx.shared.proxy_settings --[[@as SettingsStore|nil]]
	if store == nil then error("Declare lua_shared_dict proxy_settings") end
	sync_path = path

	local text, read_error = read_file(path)
	if read_error then error(read_error) end
	if text ~= nil then
		local parsed, reason = sync.parse(text)
		if parsed == nil then error(reason) end
		settings.replace(store, text)
	end
end

--- Start to check the file for a new sync.json, in one worker only. Call from init_worker_by_lua_block.
---@return nil
function main.start()
	if config == nil or store == nil or sync_path == nil then error("main.init was not called") end
	if ngx.worker.id() ~= 0 then return end

	local path = sync_path
	local interval = config.config_refresh_seconds

	local function check(premature)
		if premature then return end
		local _, reason = settings.reload(store --[[@as SettingsStore]], path, read_file)
		if reason then ngx.log(ngx.ERR, "Could not load sync.json: ", reason) end
		ngx.timer.at(interval, check)
	end

	ngx.timer.at(interval, check)
end

--- Serve one request. Call from content_by_lua_block.
---@return nil
function main.handle()
	if config == nil or store == nil then error("main.init was not called") end
	handler.handle(settings.current(store, cache), config)
end

return main
