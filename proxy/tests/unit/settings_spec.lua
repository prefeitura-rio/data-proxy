local helpers = require("tests.unit.helpers")
local settings = require("proxy.settings")

local check = helpers.check(describe, it)

local URL = "http://console/config/sync.json"
local PEOPLE_JSON = '{"schemas":{"test":{"tables":[{"name":"project.test.people","cache_ttl":42}]}}}'

--- A shared dictionary that works like ngx.shared: get, set and incr, with the values kept in a table.
---@param values table|nil The values at the start.
---@return table dict
local function dict_with(values)
	local data = values or {}
	return {
		data = data,
		get = function(_, key) return data[key] end,
		set = function(_, key, value)
			data[key] = value
			return true
		end,
		incr = function(_, key, delta, init)
			data[key] = (data[key] or init) + delta
			return data[key]
		end,
	}
end

--- Reload the settings with a console that gives one answer, and report what happened.
---@param case table
---@return table outcome
local function reload_with(case)
	local dict = dict_with(case.stored)
	local result, reason = settings.reload(dict, URL, function(url)
		assert.same(URL, url)
		return case.response, case.failure
	end)
	return { result = result, reason = reason, stored = dict.data }
end

check("settings.reload", reload_with, {
	{
		name = "stores the document and the revision and moves the version",
		response = { status = 200, body = PEOPLE_JSON, etag = '"3"' },
		expected = {
			result = { changed = true, revision = '"3"' },
			stored = { document = PEOPLE_JSON, revision = '"3"', version = 1 },
		},
	},
	{
		name = "moves the version again for a new revision",
		stored = { document = "{}", revision = '"3"', version = 4 },
		response = { status = 200, body = PEOPLE_JSON, etag = '"4"' },
		expected = {
			result = { changed = true, revision = '"4"' },
			stored = { document = PEOPLE_JSON, revision = '"4"', version = 5 },
		},
	},
	{
		name = "changes nothing when the console gives the revision that the proxy already has",
		stored = { document = PEOPLE_JSON, revision = '"3"', version = 4 },
		response = { status = 200, body = PEOPLE_JSON, etag = '"3"' },
		expected = {
			result = { changed = false, revision = '"3"' },
			stored = { document = PEOPLE_JSON, revision = '"3"', version = 4 },
		},
	},
	{
		name = "keeps the settings when the console cannot be reached",
		stored = { document = PEOPLE_JSON, revision = '"3"', version = 4 },
		failure = "connection refused",
		expected = { reason = "connection refused", stored = { document = PEOPLE_JSON, revision = '"3"', version = 4 } },
	},
	{
		name = "keeps the settings when the console answers with an error",
		stored = { document = PEOPLE_JSON, revision = '"3"', version = 4 },
		response = { status = 503, body = "", etag = nil },
		expected = {
			reason = "The console answered 503",
			stored = { document = PEOPLE_JSON, revision = '"3"', version = 4 },
		},
	},
	{
		name = "keeps the settings when the document does not match",
		stored = { document = PEOPLE_JSON, revision = '"3"', version = 4 },
		response = { status = 200, body = '{"noCachePaths":[1]}', etag = '"9"' },
		expected = {
			reason = 'Invalid sync.json: field "noCachePaths": array item 1: expected type "string", got "number"',
			stored = { document = PEOPLE_JSON, revision = '"3"', version = 4 },
		},
	},
})

--- Read the settings that a worker would use, after the given shared state.
---@param case table
---@return SyncConfig
local function current_with(case)
	local dict = dict_with(case.stored)
	local cache = case.cache or {}
	return settings.current(dict, cache)
end

check("settings.current", current_with, {
	{
		name = "gives the defaults when nothing is stored",
		expected = { tables = {}, disabled_endpoints = { "/access_policy" } },
	},
	{
		name = "gives the defaults when the stored document is not valid",
		stored = { document = "not json", version = 1 },
		expected = { tables = {}, disabled_endpoints = { "/access_policy" } },
	},
	{
		name = "parses the stored document",
		stored = { document = PEOPLE_JSON, version = 1 },
		expected = { tables = { people = { cache_ttl = 42 } }, disabled_endpoints = { "/access_policy" } },
	},
	{
		name = "parses again when the version moved",
		stored = { document = PEOPLE_JSON, version = 2 },
		cache = { version = 1, value = { tables = {}, disabled_endpoints = {} } },
		expected = { tables = { people = { cache_ttl = 42 } }, disabled_endpoints = { "/access_policy" } },
	},
	{
		name = "keeps the parsed settings while the version stays",
		stored = { document = PEOPLE_JSON, version = 2 },
		cache = { version = 2, value = { tables = {}, disabled_endpoints = { "/kept" } } },
		expected = { tables = {}, disabled_endpoints = { "/kept" } },
	},
})
