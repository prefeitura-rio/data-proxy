local Redis = require("proxy.redis")
local helpers = require("tests.unit.helpers")

local check = helpers.check(describe, it)

--- A store whose connection returns the given raw value for every key. false means that the connection fails.
---@param raw string|nil|false
---@return Redis store
local function store_with(raw)
	local store = Redis({})
	store.open = function()
		if raw == false then return nil end
		return { get = function() return raw end }
	end
	store.release = function() end
	return store
end

check("redis.read", function(case) return store_with(case.raw):read("key") end, {
	{
		name = "returns a stored entry",
		raw = '{"body":"[1]","headers":{"X-Source":"ducklake"}}',
		expected = { body = "[1]", headers = { ["X-Source"] = "ducklake" } },
	},
	{ name = "returns nothing for a missing key", expected = helpers.NULL },
	{ name = "returns nothing for a null reply", raw = ngx.null, expected = helpers.NULL },
	{ name = "returns nothing when the connection fails", raw = false, expected = helpers.NULL },
	{ name = "returns nothing for text that is not JSON", raw = "{oops", expected = helpers.NULL },
	{ name = "returns nothing for a JSON text that is not an object", raw = '"text"', expected = helpers.NULL },
	{ name = "returns nothing for a JSON null", raw = "null", expected = helpers.NULL },
	{ name = "returns nothing for an entry without a body", raw = '{"headers":{}}', expected = helpers.NULL },
	{ name = "returns nothing for an entry with a body that is not text", raw = '{"body":1}', expected = helpers.NULL },
})

--- Run the steps of a case on a fresh store, and return the warnings that the store wrote.
---@param case table
---@return string[] warnings
local function warnings_of(case)
	package.loaded["proxy.redis"] = nil
	local FreshRedis = require("proxy.redis")
	local warnings, clock = {}, 0
	local original_log, original_now = ngx.log, ngx.now
	rawset(ngx, "log", function(_, ...) warnings[#warnings + 1] = table.concat({ ... }) end)
	rawset(ngx, "now", function() return clock end)

	local store = FreshRedis({})
	store.open = function()
		if case.open_error then return nil, case.open_error end
		return {
			get = function() return case.get_reply, case.get_error end,
			setex = function() return case.setex_reply, case.setex_error end,
		}
	end
	store.release = function() end
	for _, step in ipairs(case.steps) do
		clock = step.at
		store[step.operation](store, "key", 60, { body = "[]" })
	end

	rawset(ngx, "log", original_log)
	rawset(ngx, "now", original_now)
	return warnings
end

check("redis.warnings", warnings_of, {
	{
		name = "warns with the reason when the connection fails",
		open_error = "connection refused",
		steps = { { at = 0, operation = "read" } },
		expected = { "Redis cache connection failed: connection refused" },
	},
	{
		name = "warns when a read fails",
		get_error = "timeout",
		steps = { { at = 0, operation = "read" } },
		expected = { "Redis cache read failed: timeout" },
	},
	{
		name = "warns when Redis rejects a write",
		setex_error = "READONLY",
		steps = { { at = 0, operation = "write" } },
		expected = { "Redis cache write failed: READONLY" },
	},
	{
		name = "warns once in the warning interval",
		open_error = "connection refused",
		steps = { { at = 0, operation = "read" }, { at = 1, operation = "write" } },
		expected = { "Redis cache connection failed: connection refused" },
	},
	{
		name = "warns again after the warning interval",
		open_error = "connection refused",
		steps = { { at = 0, operation = "read" }, { at = 1000, operation = "write" } },
		expected = {
			"Redis cache connection failed: connection refused",
			"Redis cache connection failed: connection refused",
		},
	},
	{
		name = "does not warn for a key that is not stored",
		get_reply = ngx.null,
		steps = { { at = 0, operation = "read" } },
		expected = {},
	},
	{
		name = "does not warn for a stored entry",
		setex_reply = "OK",
		steps = { { at = 0, operation = "write" } },
		expected = {},
	},
})
