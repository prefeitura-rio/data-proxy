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
