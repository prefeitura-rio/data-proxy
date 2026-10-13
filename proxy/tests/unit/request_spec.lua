---@diagnostic disable: missing-fields

local cjson = require("cjson.safe")
local constants = require("proxy.constants")
local helpers = require("tests.unit.helpers")
local parser = require("proxy.parser.main")
local request = require("proxy.request")

local check = helpers.check(describe, it)

--- A bearer header whose payload is the given claims, in base64url, as a real JWT has it.
---@param claims table
---@return string
local function bearer(claims)
	local payload = helpers.encode_url(assert(cjson.encode(claims)))
	return "Bearer header." .. payload .. ".signature"
end

check("request.normalize_accept", function(case) return request.normalize_accept(case.value) end, {
	{ name = "keeps a plain media type", value = "text/csv", expected = "text/csv" },
	{ name = "removes parameters", value = "application/json; charset=utf-8", expected = "application/json" },
	{ name = "uses the first media range", value = "text/csv, application/json;q=0.9", expected = "text/csv" },
	{ name = "makes lower case and trims", value = "  Application/JSON ", expected = "application/json" },
	{ name = "uses the default for */*", value = "*/*", expected = "application/json" },
	{ name = "uses the default for an empty value", value = "", expected = "application/json" },
	{ name = "uses the default for a missing value", value = nil, expected = "application/json" },
})

local filter_json = cjson.encode(parser.parse("id=eq.1"))

check("request.make_headers", function(case) return request.make_headers(case.input) end, {
	{
		name = "forwards only the allowed headers",
		input = {
			method = "POST",
			args = "",
			headers_in = { Authorization = "Bearer t", ["X-Not-Allowed"] = "secret" },
		},
		expected = { Authorization = "Bearer t" },
	},
	{
		name = "adds the filter header for a read with a filter",
		input = { method = "GET", args = "id=eq.1", headers_in = { Accept = "text/csv" } },
		expected = { Accept = "text/csv", ["X-DuckLake-Filter"] = filter_json },
	},
	{
		name = "replaces a filter header that the client sent",
		input = { method = "GET", args = "id=eq.1", headers_in = { ["X-DuckLake-Filter"] = "client-value" } },
		expected = { ["X-DuckLake-Filter"] = filter_json },
	},
	{
		name = "does not add the filter header for a write",
		input = { method = "PATCH", args = "id=eq.1", headers_in = {} },
		expected = {},
	},
	{
		name = "does not add the filter header when the query has no filter",
		input = { method = "GET", args = "select=id", headers_in = {} },
		expected = {},
	},
	{ name = "accepts a missing query string", input = { method = "GET", headers_in = {} }, expected = {} },
})

describe("request.make_headers", function()
	it("does not change the input headers", function()
		local headers_in = { Authorization = "Bearer t", ["X-DuckLake-Filter"] = "client-value" }
		request.make_headers({ method = "GET", args = "id=eq.1", headers_in = headers_in })
		assert.same({ Authorization = "Bearer t", ["X-DuckLake-Filter"] = "client-value" }, headers_in)
	end)

	describe("when a read with a filter is the first call", function()
		local forwarded

		before_each(function()
			forwarded = constants.headers.forwarded
			constants.headers.forwarded = { unpack(forwarded) }
		end)

		after_each(function() constants.headers.forwarded = forwarded end)

		it("does not change the list of forwarded headers", function()
			local before = #constants.headers.forwarded
			request.make_headers({ method = "GET", args = "id=eq.1", headers_in = {} })
			assert.equal(before, #constants.headers.forwarded)
		end)

		it("gives the same headers for every read with a filter", function()
			local input = { method = "GET", args = "id=eq.1", headers_in = {} }
			request.make_headers(input)
			assert.same({ ["X-DuckLake-Filter"] = filter_json }, request.make_headers(input))
		end)
	end)
end)

check("request.jwt_identity", function(case) return request.jwt_identity(case.header) end, {
	{
		name = "uses preferred_username first",
		header = bearer({ preferred_username = "alice", sub = "x" }),
		expected = { sub = "alice", schemas = "" },
	},
	{
		name = "uses sub when there is no preferred_username",
		header = bearer({ sub = "bob" }),
		expected = { sub = "bob", schemas = "" },
	},
	{
		name = "joins a schemas array with commas",
		header = bearer({ sub = "bob", schemas = { "a", "b" } }),
		expected = { sub = "bob", schemas = "a,b" },
	},
	{
		name = "keeps a schemas text",
		header = bearer({ sub = "bob", schemas = "a" }),
		expected = { sub = "bob", schemas = "a" },
	},
	{
		name = "is anonymous if sub is not a text",
		header = bearer({ sub = 5 }),
		expected = { sub = "anon", schemas = "" },
	},
	{ name = "is anonymous without a header", header = nil, expected = { sub = "anon", schemas = "" } },
	{
		name = "is anonymous for a header that is not a bearer token",
		header = "Basic abc",
		expected = { sub = "anon", schemas = "" },
	},
	{
		name = "is anonymous when the payload is not JSON",
		header = "Bearer header.not-json.signature",
		expected = { sub = "anon", schemas = "" },
	},
	{
		name = "is anonymous when the payload is JSON but not an object",
		header = "Bearer header.5.signature",
		expected = { sub = "anon", schemas = "" },
	},
})

local cfg = { tables = { people = { cache_ttl = 42 } }, disabled_endpoints = { "/private" } }
local route = { postgrest = "http://read", retry_postgrest = "http://write" }
--- Make the context of one request with the config and the fixed clock of the cases.
---@param input table
---@return RequestContext
local function context_of(input) return request.make_context(input, route, { config = cfg, now = 1234.5 }) end

check("request.make_context", function(case) return context_of(case.input)[case.field] end, {
	{
		name = "uses the cache time of the table",
		input = { method = "GET", uri = "/people", headers_in = {}, cache_ttl = "60" },
		field = "cache_ttl",
		expected = "42",
	},
	{
		name = "uses the nginx cache time for a table without settings",
		input = { method = "GET", uri = "/other", headers_in = {}, cache_ttl = "60" },
		field = "cache_ttl",
		expected = "60",
	},
	{
		name = "gives an empty cache time when nothing sets it",
		input = { method = "GET", uri = "/other", headers_in = {} },
		field = "cache_ttl",
		expected = "",
	},
	{
		name = "uses the time that the caller gives",
		input = { method = "GET", uri = "/other", headers_in = {} },
		field = "started",
		expected = 1234.5,
	},
	{
		name = "uses the disabled endpoints of the configuration",
		input = { method = "GET", uri = "/other", headers_in = {} },
		field = "disabled_endpoints",
		expected = { "/private" },
	},
	{
		name = "reads max_body as a number, or 0 if it is not a number",
		input = { method = "GET", uri = "/other", headers_in = {}, max_body = "bad" },
		field = "max_body",
		expected = 0,
	},
	{
		name = "copies the routes",
		input = { method = "GET", uri = "/other", headers_in = {} },
		field = "retry_postgrest",
		expected = "http://write",
	},
	{
		name = "copies the retry count from the nginx request",
		input = { method = "GET", uri = "/other", headers_in = {}, retries = 3, backoff_ms = 100 },
		field = "retries",
		expected = 3,
	},
	{
		name = "copies the backoff from the nginx request",
		input = { method = "GET", uri = "/other", headers_in = {}, retries = 3, backoff_ms = 100 },
		field = "backoff_ms",
		expected = 100,
	},
	{
		name = "reads the Accept-Profile header",
		input = { method = "GET", uri = "/other", headers_in = { ["Accept-Profile"] = "public" } },
		field = "profile",
		expected = "public",
	},
})

--- A request identity with every field set. Each case changes one field.
---@return CacheKeyParts
local function key_parts()
	return {
		method = "GET",
		uri = "/t",
		args = "id=eq.1",
		sub = "user",
		schemas = "test",
		profile = "test",
		headers = {},
	}
end

describe("request.cache_identity", function()
	--- The identity of the same request with one field changed.
	---@param change fun(parts: CacheKeyParts)
	---@return string[]
	local function identity_with(change)
		local parts = key_parts()
		change(parts)
		return request.cache_identity(parts)
	end

	it("lists the values in the order of the key format", function()
		local parts = key_parts()
		parts.headers =
			{ Range = "items=0-1", Prefer = "count=exact", Accept = "text/csv", ["X-DuckLake-Snapshot"] = "7" }
		assert.same(
			{ "GET", "/t", "id=eq.1", "user", "test", "test", "items=0-1", "count=exact", "text/csv", "7" },
			request.cache_identity(parts)
		)
	end)

	it(
		"uses empty texts and the default media type for missing headers",
		function()
			assert.same(
				{ "GET", "/t", "id=eq.1", "user", "test", "test", "", "", "application/json", "" },
				request.cache_identity(key_parts())
			)
		end
	)

	it("is the same when the Accept header means the same media type", function()
		local plain = identity_with(function(parts) parts.headers.Accept = "application/json" end)
		local noisy = identity_with(function(parts) parts.headers.Accept = "Application/JSON; charset=utf-8" end)
		assert.same(plain, noisy)
	end)

	local base = request.cache_identity(key_parts())

	check("request.cache_identity changes", function(case)
		local parts = key_parts()
		case.change(parts)
		return request.cache_identity(parts)
	end, {
		{ name = "the method", change = function(parts) parts.method = "HEAD" end, not_expected = base },
		{ name = "the path", change = function(parts) parts.uri = "/other" end, not_expected = base },
		{ name = "the query", change = function(parts) parts.args = "id=eq.2" end, not_expected = base },
		{ name = "the user", change = function(parts) parts.sub = "other" end, not_expected = base },
		{ name = "the schemas", change = function(parts) parts.schemas = "other" end, not_expected = base },
		{ name = "the profile", change = function(parts) parts.profile = "other" end, not_expected = base },
		{
			name = "the Range header",
			change = function(parts) parts.headers.Range = "items=0-1" end,
			not_expected = base,
		},
		{
			name = "the Prefer header",
			change = function(parts) parts.headers.Prefer = "count=exact" end,
			not_expected = base,
		},
		{
			name = "the Accept media type",
			change = function(parts) parts.headers.Accept = "text/csv" end,
			not_expected = base,
		},
		{
			name = "the snapshot header",
			change = function(parts) parts.headers["X-DuckLake-Snapshot"] = "7" end,
			not_expected = base,
		},
	})
end)

describe("request.cache_key", function()
	it("is 64 hexadecimal characters", function()
		local key = request.cache_key(key_parts())
		assert.matches("^%x+$", key)
		assert.equal(64, #key)
	end)
end)
