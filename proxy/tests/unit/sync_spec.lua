local helpers = require("tests.unit.helpers")
local sync = require("proxy.sync")

local check = helpers.check(describe, it)

--- A sync.json with one table, people, that has a cache time of 42 seconds.
local PEOPLE_JSON = '{"schemas":{"test":{"tables":[{"name":"project.test.people","cache_ttl":42}]}}}'

--- Write a text to a temporary file.
---@param content string
---@return string path The caller removes the file.
local function temporary_file(content)
	local path = os.tmpname()
	local file = assert(io.open(path, "w"))
	file:write(content)
	file:close()
	return path
end

--- Read sync.json from a temporary file, as the proxy does at start-up.
---@param content string
---@return SyncConfig
local function sync_from(content)
	local path = temporary_file(content)
	local loaded = sync.load(path)
	os.remove(path)
	return loaded
end

check("sync.load", function(case) return sync_from(case.content) end, {
	{
		name = "reads the cache time of a table by its short name",
		content = PEOPLE_JSON,
		expected = { tables = { people = { cache_ttl = 42 } }, disabled_endpoints = { "/access_policy" } },
	},
	{
		name = "gives empty tables and the default paths for an empty document",
		content = "{}",
		expected = { tables = {}, disabled_endpoints = { "/access_policy" } },
	},
	{
		name = "uses the no-cache paths of the file",
		content = '{"noCachePaths":["/private"]}',
		expected = { tables = {}, disabled_endpoints = { "/private" } },
	},
	{
		name = "skips a table whose name has no short form",
		content = '{"schemas":{"test":{"tables":[{"name":"project.test.","cache_ttl":42}]}}}',
		expected = { tables = {}, disabled_endpoints = { "/access_policy" } },
	},
})

check("sync.parse", function(case) return sync.parse(case.content) end, {
	{
		name = "reads the cache time of a table from the text of a document",
		content = PEOPLE_JSON,
		expected = { tables = { people = { cache_ttl = 42 } }, disabled_endpoints = { "/access_policy" } },
	},
	{
		name = "uses the no-cache paths of the document",
		content = '{"noCachePaths":["/private"]}',
		expected = { tables = {}, disabled_endpoints = { "/private" } },
	},
	{
		name = "reads the revision of the access policy and the rules",
		content = '{"revision":12}',
		expected = { tables = {}, disabled_endpoints = { "/access_policy" }, revision = 12 },
	},
	{ name = "gives nil and a reason for text that is not JSON", content = "not json", reason = "Expected" },
	{
		name = "gives nil and a reason that names sync.json for a document that does not match",
		content = '{"noCachePaths":[1]}',
		reason = 'Invalid sync.json: field "noCachePaths"',
	},
})

describe("the document that console.config() writes", function()
	-- The file is the golden output of console.config() for fixed rows. A pytest case in sync/tests checks that the
	-- database still writes it, so the proxy and the database cannot drift apart.
	local path = "tests/unit/fixtures/console_config.json"

	it("is a document that the proxy takes", function()
		local config = sync.load(path)
		assert.is_not_nil(config)
	end)

	it("gives the cache time of each table by its short name, and none for a table that has no time", function()
		local config = sync.load(path)
		assert.same({ staff = {}, events = {}, orders = { cache_ttl = 60 } }, config.tables)
	end)

	it("carries the revision of the rules and keeps the default paths that are never cached", function()
		local config = sync.load(path)
		assert.equal(7, config.revision)
		assert.same({ "/access_policy" }, config.disabled_endpoints)
	end)
end)

describe("sync module state", function()
	it("loads into the returned value, not into the module", function()
		sync_from(PEOPLE_JSON)
		assert.is_nil(rawget(sync, "tables"))
	end)
end)

check("sync.load errors", function(case)
	local path = case.content and temporary_file(case.content) or "/nonexistent/sync.json"
	local ok, message = pcall(sync.load, path)
	os.remove(path)
	if ok then return true end
	return nil, message
end, {
	{ name = "raises an error for a file that is missing", content = nil, reason = "No such file" },
	{ name = "raises an error for JSON that is not valid", content = "not json", reason = "Expected" },
	{
		name = "raises an error that names sync.json for a document that does not match",
		content = '{"noCachePaths":[1]}',
		reason = 'Invalid sync.json: field "noCachePaths"',
	},
})

local loaded = sync_from(PEOPLE_JSON)

check("sync.table_for", function(case)
	local settings = sync.table_for(loaded, case.uri)
	return settings and settings.cache_ttl
end, {
	{ name = "finds a table by the first path segment", uri = "/people", expected = 42 },
	{ name = "ignores the query string", uri = "/people?id=eq.1", expected = 42 },
	{ name = "ignores deeper path segments", uri = "/people/1", expected = 42 },
	{ name = "gives nil for an unknown table", uri = "/unknown", expected = helpers.NULL },
})

describe("sync.empty", function()
	it("gives the settings of an empty document", function() assert.same(sync.parse("{}"), sync.empty()) end)

	it("gives a new table on each call, so a caller cannot change the next one", function()
		local first = sync.empty()
		first.tables.people = { cache_ttl = 1 }
		assert.same({}, sync.empty().tables)
	end)
end)
