-- Entry points that nginx calls: init once, handle for each request. The only module with state.

local handler = require("proxy.handler")
local sync = require("proxy.sync")

--- The config from the environment. init loads it.
---@type Config|nil
local config

--- The table settings that init loaded. nil before init.
---@type SyncConfig|nil
local sync_config

--- The entry points that nginx calls.
---@class Main
local main = {}

--- Load sync.json and the config. If a file or a variable is not valid, the function raises an error,
--- and nginx does not start. Call from init_by_lua_block.
---@param path string Path of sync.json.
---@param getenv (fun(name: string): string|nil)|nil Gives the text of a variable. Default: os.getenv.
---@return nil
function main.init(path, getenv)
	sync_config = sync.load(path)
	config = require("proxy.config").load(getenv or os.getenv)
end

--- Serve one request. Call from content_by_lua_block.
---@return nil
function main.handle()
	if config == nil or sync_config == nil then error("main.init was not called") end
	handler.handle(sync_config, config)
end

return main
