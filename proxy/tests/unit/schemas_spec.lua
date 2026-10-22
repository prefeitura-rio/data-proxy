local cjson = require("cjson.safe")
local helpers = require("tests.unit.helpers")
local schemas = require("proxy.schemas.main")
local tablex = require("pl.tablex") --[[@as pl.tablex]]

local check = helpers.check(describe, it)

--- The names of a table, in order, so the names of two tables can be compared.
---@param source table
---@return string[]
local function names_of(source)
	local names = {}
	tablex.foreach(source, function(_, name) names[#names + 1] = name end)
	table.sort(names)
	return names
end

--- A sync.json with one schema, s, that holds the given tables.
---@param tables table[]
---@return table
local function sync_json(tables) return { schemas = { s = { tables = tables } } } end

local required = {
	redis_host = "redis",
	postgrest = "http://postgrest",
}

--- The text of the required variables, with the changes of the case.
---@param changes table<string, string|false>|nil A false removes the variable.
---@return table<string, string>
local function text_of(changes)
	local text = tablex.update({}, required)
	tablex.foreach(changes or {}, function(value, name) text[name] = value or nil end)
	return text
end

check("schemas.cached_entry", function(case) return schemas.cached_entry:validate(case.value) end, {
	{
		name = "accepts headers",
		valid = true,
		value = { body = "[1]", headers = { ["X-Source"] = "ducklake" } },
	},
	{
		name = "accepts a field that the schema does not name",
		valid = true,
		value = { body = "[1]", extra = 1 },
	},
	{ name = "rejects a body that is not a text", value = { body = 1 }, reason = 'field "body"' },
	{ name = "rejects a missing body", value = { headers = {} }, reason = 'field "body"' },
	{ name = "rejects a header that is a number", value = { body = "x", headers = { a = 1 } }, reason = "headers" },
	{
		name = "rejects JSON null headers",
		value = { body = "x", headers = cjson.null },
		reason = 'field "headers"',
	},
})

check("schemas.sync_document", function(case) return schemas.sync_document:validate(case.value) end, {
	{
		name = "accepts paths and tables",
		valid = true,
		value = {
			noCachePaths = { "/private" },
			schemas = { sales = { tables = { { name = "project.sales.orders", cache_ttl = 60 } } } },
		},
	},
	{
		name = "accepts a field that the schema does not name",
		valid = true,
		value = { other = true, schemas = { sales = { x = 1 } } },
	},
	{ name = "accepts a table without cache_ttl", valid = true, value = sync_json({ { name = "p.s.t" } }) },
	{ name = "accepts a revision", valid = true, value = { revision = 3 } },
	{ name = "rejects a revision that is not a whole number", value = { revision = "3" }, reason = 'field "revision"' },
	{ name = "rejects a path that is not a text", value = { noCachePaths = { 1 } }, reason = 'field "noCachePaths"' },
	{ name = "rejects a table without a name", value = sync_json({ {} }), reason = "name" },
	{ name = "rejects an empty name", value = sync_json({ { name = "" } }), reason = "name" },
	{
		name = "rejects a negative cache_ttl",
		value = sync_json({ { name = "t", cache_ttl = -1 } }),
		reason = 'field "cache_ttl"',
	},
	{
		name = "rejects a cache_ttl that is not a whole number",
		value = sync_json({ { name = "t", cache_ttl = 1.5 } }),
		reason = 'field "cache_ttl"',
	},
	{ name = "rejects a schema that is not an object", value = { schemas = { s = "x" } }, reason = "expected type" },
})

check("schemas.jwt_claims", function(case) return schemas.jwt_claims:validate(case.value) end, {
	{
		name = "accepts the claims that the proxy reads",
		valid = true,
		value = { sub = "id", preferred_username = "ana", schemas = { "sales", "hr" } },
	},
	{ name = "accepts schemas as a text", valid = true, value = { schemas = "sales,hr" } },
	{ name = "accepts a claim that the schema does not name", valid = true, value = { iss = "keycloak" } },
	{ name = "rejects a sub that is not a text", value = { sub = 1 }, reason = 'field "sub"' },
	{ name = "rejects schemas that are a number", value = { schemas = 1 }, reason = 'field "schemas"' },
	{ name = "rejects a list with a number", value = { schemas = { "a", 1 } }, reason = 'field "schemas"' },
})

check("schemas.config rejects", function(case) return schemas.config:validate(text_of(case.changes)) end, {
	{ name = "rejects a ttl that is not a number", changes = { cache_ttl = "abc" }, reason = 'field "cache_ttl"' },
	{ name = "rejects a ttl in hexadecimal", changes = { cache_ttl = "0x10" }, reason = 'field "cache_ttl"' },
	{ name = "rejects a ttl that is not whole", changes = { cache_ttl = "1.5" }, reason = 'field "cache_ttl"' },
	{ name = "rejects a negative ttl", changes = { cache_ttl = "-1" }, reason = 'field "cache_ttl"' },
	{ name = "rejects port 0", changes = { redis_port = "0" }, reason = 'field "redis_port"' },
	{ name = "rejects a port over 65535", changes = { redis_port = "70000" }, reason = 'field "redis_port"' },
	{ name = "rejects a negative database", changes = { redis_database = "-1" }, reason = 'field "redis_database"' },
	{ name = "rejects an empty host", changes = { redis_host = "" }, reason = 'field "redis_host"' },
	{ name = "rejects a missing host", changes = { redis_host = false }, reason = 'field "redis_host"' },
	{ name = "rejects a missing URL", changes = { postgrest = false }, reason = 'field "postgrest"' },
	{
		name = "rejects a refresh time of 0",
		changes = { config_refresh_seconds = "0" },
		reason = 'field "config_refresh_seconds"',
	},
	{ name = "rejects an empty console URL", changes = { config_url = "" }, reason = 'field "config_url"' },
})

describe("schemas.config", function()
	it("gives every field of the Config type, and only those", function()
		---@type table<string, string>
		local full_text = {
			cache_ttl = "60",
			max_body = "1000",
			redis_host = "redis",
			redis_port = "6380",
			redis_database = "2",
			redis_password = "secret",
			postgrest = "http://postgrest",
			max_inflight = "64",
			config_url = "http://console/config/sync.json",
			config_refresh_seconds = "30",
		}
		---@type Config
		local expected = {
			cache_ttl = 60,
			max_body = 1000,
			redis_host = "redis",
			redis_port = 6380,
			redis_database = 2,
			redis_password = "secret",
			postgrest = "http://postgrest",
			max_inflight = 64,
			config_url = "http://console/config/sync.json",
			config_refresh_seconds = 30,
		}

		assert.same(expected, schemas.config:validate(full_text))
		assert.same(names_of(expected), names_of(schemas.config.fields))
	end)

	it("applies the defaults", function()
		local config = assert(schemas.config:validate(text_of()))
		assert.same(300, config.cache_ttl)
		assert.same(262144, config.max_body)
		assert.same(6379, config.redis_port)
		assert.same(1, config.redis_database)
		assert.is_nil(config.redis_password)
		assert.same(0, config.max_inflight)
		assert.is_nil(config.config_url)
		assert.same(60, config.config_refresh_seconds)
	end)

	it("does not change the text that it reads", function()
		local text = text_of({ cache_ttl = "60" })
		schemas.config:validate(text)
		assert.same(text_of({ cache_ttl = "60" }), text)
	end)
end)

local reason = 'field "where"'

--- A comparison node of the filter grammar, with the changes of the case.
---@param changes table|nil
---@return table
local function comparison(changes)
	return tablex.update(
		{ kind = "comparison", column = "id", operator = "eq", negated = false, value = "42" },
		changes or {}
	)
end

check("schemas.filter_document", function(case) return schemas.filter_document:validate(case.value) end, {
	{ name = "accepts a scalar comparison", valid = true, value = { where = comparison() } },
	{
		name = "accepts an in list",
		valid = true,
		value = { where = comparison({ operator = "in", value = { "1", "2" } }) },
	},
	{
		name = "accepts an is keyword",
		valid = true,
		value = { where = comparison({ column = "done", operator = "is", value = "null" }) },
	},
	{
		name = "accepts a group that holds a group",
		valid = true,
		value = {
			where = {
				kind = "group",
				operator = "and",
				items = { comparison(), { kind = "group", operator = "or", items = { comparison() }, negated = true } },
			},
		},
	},
	{
		name = "rejects an operator that the grammar does not know",
		value = { where = comparison({ operator = "nope" }) },
		reason = reason,
	},
	{
		name = "rejects a scalar for the is operator",
		value = { where = comparison({ column = "done", operator = "is", value = "maybe" }) },
		reason = reason,
	},
	{
		name = "rejects an empty in list",
		value = { where = comparison({ operator = "in", value = {} }) },
		reason = reason,
	},
	{
		name = "rejects an empty group",
		value = { where = { kind = "group", operator = "and", items = {} } },
		reason = reason,
	},
	{
		name = "rejects a field that the shape does not name",
		value = { where = comparison({ extra = 1 }) },
		reason = reason,
	},
	{
		name = "rejects a negated that is not a boolean",
		value = { where = comparison({ negated = "yes" }) },
		reason = reason,
	},
	{
		name = "rejects a column that is not a column name",
		value = { where = comparison({ column = "2id" }) },
		reason = reason,
	},
	{
		name = "rejects a group that holds a node of the wrong shape",
		value = { where = { kind = "group", operator = "and", items = { comparison(), { kind = "comparison" } } } },
		reason = reason,
	},
})
