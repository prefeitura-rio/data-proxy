local config = require("proxy.config")
local helpers = require("tests.unit.helpers")

local check = helpers.check(describe, it)

local REQUIRED = { PROXY_REDIS_HOST = "cache", PROXY_POSTGREST = "http://127.0.0.1:3000" }

--- Give the environment of a case: the two required variables, then the variables of the case.
--- A variable of the case with the value false is removed.
---@param case table
---@return fun(name: string): string|nil
local function getenv_of(case)
	local variables = {}
	for name, value in pairs(REQUIRED) do
		variables[name] = value
	end
	for name, value in pairs(case.env or {}) do
		variables[name] = value or nil
	end
	return function(name) return variables[name] end
end

check("config.load setting", function(case) return config.load(getenv_of(case))[case.field] end, {
	{ name = "defaults the cache lifetime to 300 seconds", field = "cache_ttl", expected = 300 },
	{ name = "defaults the body limit to 262144 bytes", field = "max_body", expected = 262144 },
	{ name = "defaults the Redis port to 6379", field = "redis_port", expected = 6379 },
	{ name = "defaults the Redis database to 1", field = "redis_database", expected = 1 },
	{ name = "defaults the inflight limit to 0, which means no limit", field = "max_inflight", expected = 0 },
	{ name = "defaults the refresh of the config to 60 seconds", field = "config_refresh_seconds", expected = 60 },
	{ name = "gives no Redis password by default", field = "redis_password", expected = helpers.NULL },
	{ name = "gives no config address by default", field = "config_url", expected = helpers.NULL },
	{ name = "reads the Redis host", field = "redis_host", expected = "cache" },
	{ name = "reads the PostgREST address", field = "postgrest", expected = "http://127.0.0.1:3000" },
	{ name = "overrides the cache lifetime", field = "cache_ttl", env = { PROXY_CACHE_TTL = "60" }, expected = 60 },
	{ name = "accepts a cache lifetime of 0", field = "cache_ttl", env = { PROXY_CACHE_TTL = "0" }, expected = 0 },
	{ name = "overrides the Redis port", field = "redis_port", env = { PROXY_REDIS_PORT = "6380" }, expected = 6380 },
	{ name = "overrides the inflight limit", field = "max_inflight", env = { PROXY_MAX_INFLIGHT = "4" }, expected = 4 },
	{
		name = "overrides the refresh of the config",
		field = "config_refresh_seconds",
		env = { PROXY_CONFIG_REFRESH_SECONDS = "5" },
		expected = 5,
	},
	{
		name = "reads the Redis password",
		field = "redis_password",
		env = { PROXY_REDIS_PASSWORD = "secret" },
		expected = "secret",
	},
	{
		name = "reads the config address",
		field = "config_url",
		env = { PROXY_CONFIG_URL = "http://postgrest/rpc/config" },
		expected = "http://postgrest/rpc/config",
	},
	{
		name = "treats an empty variable as a missing one",
		field = "cache_ttl",
		env = { PROXY_CACHE_TTL = "" },
		expected = 300,
	},
})

check("config.load refusal", function(case)
	local loaded, result = pcall(config.load, getenv_of(case))
	if loaded then return result end
	return nil, result
end, {
	{ name = "refuses a missing Redis host", env = { PROXY_REDIS_HOST = false }, reason = "Invalid config" },
	{ name = "refuses a missing PostgREST address", env = { PROXY_POSTGREST = false }, reason = "Invalid config" },
	{ name = "refuses an empty Redis host", env = { PROXY_REDIS_HOST = "" }, reason = "Invalid config" },
	{ name = "refuses a cache lifetime that is not a number", env = { PROXY_CACHE_TTL = "abc" }, reason = "cache_ttl" },
	{ name = "refuses a negative cache lifetime", env = { PROXY_CACHE_TTL = "-1" }, reason = "cache_ttl" },
	{ name = "refuses a body limit with a decimal point", env = { PROXY_MAX_BODY = "1.5" }, reason = "max_body" },
	{ name = "refuses the Redis port 0", env = { PROXY_REDIS_PORT = "0" }, reason = "redis_port" },
	{ name = "refuses a Redis port over 65535", env = { PROXY_REDIS_PORT = "65536" }, reason = "redis_port" },
	{
		name = "refuses a Redis database that is not a number",
		env = { PROXY_REDIS_DATABASE = "x" },
		reason = "redis_database",
	},
	{
		name = "refuses an inflight limit that is not a number",
		env = { PROXY_MAX_INFLIGHT = "two" },
		reason = "max_inflight",
	},
	{
		name = "refuses a refresh of 0 seconds",
		env = { PROXY_CONFIG_REFRESH_SECONDS = "0" },
		reason = "config_refresh_seconds",
	},
})
