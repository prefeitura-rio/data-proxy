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
		expected = { "Proxy Redis cache failed: operation=connection error=connection refused" },
	},
	{
		name = "warns when a read fails",
		get_error = "timeout",
		steps = { { at = 0, operation = "read" } },
		expected = { "Proxy Redis cache failed: operation=read error=timeout" },
	},
	{
		name = "warns when Redis rejects a write",
		setex_error = "READONLY",
		steps = { { at = 0, operation = "write" } },
		expected = { "Proxy Redis cache failed: operation=write error=READONLY" },
	},
	{
		name = "warns once in the warning interval",
		open_error = "connection refused",
		steps = { { at = 0, operation = "read" }, { at = 1, operation = "write" } },
		expected = { "Proxy Redis cache failed: operation=connection error=connection refused" },
	},
	{
		name = "warns again after the warning interval",
		open_error = "connection refused",
		steps = { { at = 0, operation = "read" }, { at = 1000, operation = "write" } },
		expected = {
			"Proxy Redis cache failed: operation=connection error=connection refused",
			"Proxy Redis cache failed: operation=connection error=connection refused",
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

--- Run one call of the store on a connection that answers with the given values. The call records the keys that
--- the store asked for in `case.asked`.
---@param case table
---@return any ...
local function generation_call(case)
	package.loaded["proxy.redis"] = nil
	local FreshRedis = require("proxy.redis")
	local store = FreshRedis({})
	store.open = function()
		if case.open_error then return nil, case.open_error end
		return {
			mget = function(_, ...)
				case.asked = { ... }
				return case.mget_reply, case.mget_error
			end,
			incr = function() return case.incr_reply, case.incr_error end,
		}
	end
	store.release = function() end
	if case.operation == "generations" then return store:generations(case.profile) end
	return store[case.operation](store)
end

--- The two counters that one call returns, as a list.
---@param case table
---@return integer[]
local function counters_of(case) return { generation_call(case) } end

check("redis.generations", counters_of, {
	{
		name = "reads the policy and the data counters",
		operation = "generations",
		profile = "sales",
		mget_reply = { "7", "3" },
		expected = { 7, 3 },
	},
	{
		name = "is 0 for a counter that nothing stored",
		operation = "generations",
		profile = "sales",
		mget_reply = { "7", ngx.null },
		expected = { 7, 0 },
	},
	{
		name = "is 0 for a value that is not a number",
		operation = "generations",
		profile = "sales",
		mget_reply = { "x", "y" },
		expected = { 0, 0 },
	},
	{
		name = "is 0 for both when the read fails",
		operation = "generations",
		profile = "sales",
		mget_error = "timeout",
		expected = { 0, 0 },
	},
	{
		name = "is 0 for both when the connection fails",
		operation = "generations",
		profile = "sales",
		open_error = "refused",
		expected = { 0, 0 },
	},
})

describe("redis.generations keys", function()
	--- The keys that the store asks for, for one profile.
	---@param profile any
	---@return string[]
	local function keys_for(profile)
		local case = { operation = "generations", profile = profile, mget_reply = { "1", "2" } }
		generation_call(case)
		return case.asked
	end

	it(
		"asks for the policy counter and the counter of the schema in one call",
		function() assert.same({ "policy:generation", "cache:generation:sales" }, keys_for("sales")) end
	)

	it(
		"asks for the counter of the empty name when there is no profile",
		function() assert.same({ "policy:generation", "cache:generation:" }, keys_for(nil)) end
	)

	it(
		"asks for the counter of the empty name when the profile is longer than a schema name can be",
		function() assert.same({ "policy:generation", "cache:generation:" }, keys_for(string.rep("a", 64))) end
	)
end)

check("redis.bump_generation", generation_call, {
	{ name = "counts up with incr", operation = "bump_generation", incr_reply = 8, expected = true },
	{ name = "reports a failed incr", operation = "bump_generation", incr_error = "READONLY", expected = false },
	{ name = "reports a failed connection", operation = "bump_generation", open_error = "refused", expected = false },
})
