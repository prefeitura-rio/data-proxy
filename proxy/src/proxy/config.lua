-- Read and check the settings from the PROXY_* environment variables.

local constants = require("proxy.constants")
local schemas = require("proxy.schemas.main")
local tablex = require("pl.tablex") --[[@as pl.tablex]]

--- The function that reads the config.
---@class ConfigModule
local config = {}

--- Turn the environment into the settings: the text of each variable, by setting name.
---
--- ```lua
--- local variables = { PROXY_CACHE_TTL = "60", PROXY_HA_MODE = "" }
--- convert_to_setting(function(name) return variables[name] end)  --> { cache_ttl = "60" }
--- ```
---@param getenv fun(name: string): string|nil Gives the text of a variable.
---@return table<string, string> text The text of each setting that has a variable, by setting name.
local function convert_to_setting(getenv)
	return tablex.pairmap(function(name)
		local text = getenv(constants.config.env_prefix .. name:upper())
		if text == nil or text == "" then return nil end
		return text, name
	end, schemas.config.fields)
end

--- Read the config from the variables, convert and check it once with schemas.config, and give it.
--- A variable is PROXY_ and the upper-case name of the setting. A setting with no variable gets its default.
---
--- ```lua
--- config.load(os.getenv).cache_ttl  --> 300
--- ```
---@param getenv fun(name: string): string|nil Gives the text of a variable.
---@return Config config
function config.load(getenv)
	local values, reason = schemas.config:validate(convert_to_setting(getenv))
	if not values then error("Invalid config: " .. reason) end
	return values
end

return config
