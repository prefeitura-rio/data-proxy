local assert = require("luassert")
local cjson = require("cjson.safe")
local tablex = require("pl.tablex") --[[@as pl.tablex]]

--- The helpers that the specs share.
---@class SpecHelpers
local helpers = {}

local FIXED_NOW = 1000
local LOG_LEVELS = { ERR = 4, WARN = 5, INFO = 7 }

--- Escape the two characters that decode_base64url maps, so a payload survives the round trip.
---@param text string
---@return string
function helpers.encode_url(text) return (text:gsub("_", "%%5F"):gsub("-", "%%2D")) end

--- Fill the ngx names that the proxy modules need, because the unit specs run without nginx.
local function install_ngx_stubs()
	_G.ngx = _G.ngx or {}
	---@type table<string, any>
	local stubs = {
		null = {},
		now = function() return FIXED_NOW end,
		log = function() end,
		sleep = function() end,
		-- utils.percent_decode calls ngx.unescape_uri.
		unescape_uri = function(value)
			return (value:gsub("%%(%x%x)", function(hex) return string.char(tonumber(hex, 16)) end))
		end,
		-- utils.encode_path calls ngx.escape_uri, and nginx keeps the / separator.
		escape_uri = function(value)
			return (value:gsub("([^%w%-%._~/])", function(char) return string.format("%%%02X", string.byte(char)) end))
		end,
		-- utils.decode_base64url calls ngx.decode_base64. Keeping the text lets a payload round trip,
		-- and giving nil for a "!" lets a spec test the nil path.
		decode_base64 = function(text)
			if text:find("!", 1, true) then return nil end
			return (text:gsub("%%5F", "_"):gsub("%%2D", "-"))
		end,
	}

	tablex.foreach(LOG_LEVELS, function(level, name) stubs[name] = level end)

	tablex.foreach(stubs, function(stub, name)
		if ngx[name] == nil then ngx[name] = stub end
	end)
end

local comparisons = {
	expected = function(case, call) assert.same(case.expected, call(case)) end,
	not_expected = function(case, call) assert.is_not.same(case.not_expected, call(case)) end,
	valid = function(case, call) assert.same(case.value, call(case)) end,
	reason = function(case, call)
		local value, reason = call(case)
		assert.is_nil(value)
		assert.matches(case.reason, reason)
	end,
}

--- The fields that select a comparison. A case without one of them uses expected.
local shapes = { "reason", "valid", "not_expected" }

--- The comparison that a case selects.
---@param case table
---@return function
local function comparison_of(case)
	local shape = "expected"

	tablex.foreach(shapes, function(field)
		if case[field] ~= nil then shape = field end
	end)

	return comparisons[shape]
end

--- Make the check of one spec file from the busted functions of that file.
--- A case uses one of the `comparisons` fields to say how to compare the value of the call.
---@param describe function
---@param it function
---@return fun(name: string, call: function, cases: table[])
function helpers.check(describe, it)
	return function(name, call, cases)
		describe(name, function()
			tablex.foreachi(cases, function(case)
				local comparison = comparison_of(case)
				it(case.name, function() comparison(case, call) end)
			end)
		end)
	end
end

--- Load the cases of a fixture file.
---@param path string
---@return table[]
function helpers.load_filter_cases(path)
	local file = assert(io.open(path, "r"))
	local content = assert(file:read("*a"))
	file:close()
	---@type table[]
	local cases = assert(cjson.decode(content))
	tablex.foreach(cases, function(case)
		if case.expected == cjson.null or case.expected == ngx.null or tostring(case.expected) == "userdata: NULL" then
			case.expected = nil
		end
	end)
	return cases
end

install_ngx_stubs()

package.preload["resty.sha256"] = function()
	return {
		new = function()
			return {
				update = function() end,
				final = function() return string.rep("x", 32) end,
			}
		end,
	}
end

package.preload["resty.string"] = function()
	return {
		to_hex = function(value)
			local hex = value:gsub(".", function(char) return string.format("%02x", string.byte(char)) end)
			return hex
		end,
	}
end

package.preload["resty.http"] = function()
	return { new = function() return {} end }
end

package.preload["resty.redis"] = function()
	return { new = function() return {} end }
end

package.preload["resty.lock"] = function()
	return { new = function() return {} end }
end

return helpers
