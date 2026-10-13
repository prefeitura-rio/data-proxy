---@diagnostic disable: missing-fields

local cache = require("proxy.cache")
local helpers = require("tests.unit.helpers")

local check = helpers.check(describe, it)

local tablex = require("pl.tablex") --[[@as pl.tablex]]

--- A request context that the cache accepts. Each case changes some fields.
---@param changes table|nil
---@return RequestContext
local function context_with(changes)
	local context = {
		method = "GET",
		uri = "/people",
		headers = {},
		disabled_endpoints = { "/access_policy" },
		max_body = 0,
		cache_ttl = "60",
	}
	return tablex.update(context, changes or {})
end

--- A response from PostgREST that the cache accepts. Each case changes some fields.
---@param changes table|nil
---@return Response
local function response_with(changes)
	local response = { status = 200, body = '[{"id":1}]', headers = { ["Content-Type"] = "application/json" } }
	return tablex.update(response, changes or {})
end

check("cache.is_cacheable", function(case) return cache.is_cacheable(case.response, case.context) end, {
	{ name = "accepts a normal JSON answer", response = response_with(), context = context_with(), expected = true },
	{
		name = "accepts an answer without a Content-Type",
		response = response_with({ headers = {} }),
		context = context_with(),
		expected = true,
	},
	{
		name = "accepts a JSON type with a charset",
		response = response_with({ headers = { ["Content-Type"] = "application/json; charset=utf-8" } }),
		context = context_with(),
		expected = true,
	},
	{ name = "rejects a missing response", response = nil, context = context_with(), expected = false },
	{
		name = "rejects a status other than 200",
		response = response_with({ status = 404 }),
		context = context_with(),
		expected = false,
	},
	{
		name = "rejects a 206 partial answer",
		response = response_with({ status = 206 }),
		context = context_with(),
		expected = false,
	},
	{
		name = "rejects an empty body",
		response = response_with({ body = "" }),
		context = context_with(),
		expected = false,
	},
	{
		name = "rejects an empty JSON array",
		response = response_with({ body = "[]" }),
		context = context_with(),
		expected = false,
	},
	{
		name = "rejects a null body",
		response = response_with({ body = "null" }),
		context = context_with(),
		expected = false,
	},
	{
		name = "rejects a non-JSON type",
		response = response_with({ headers = { ["Content-Type"] = "text/plain" } }),
		context = context_with(),
		expected = false,
	},
	{
		name = "rejects a POST",
		response = response_with(),
		context = context_with({ method = "POST" }),
		expected = false,
	},
	{
		name = "rejects a HEAD",
		response = response_with(),
		context = context_with({ method = "HEAD" }),
		expected = false,
	},
	{
		name = "rejects an excluded path",
		response = response_with(),
		context = context_with({ uri = "/access_policy/1" }),
		expected = false,
	},
	{
		name = "rejects a range request",
		response = response_with(),
		context = context_with({ headers = { Range = "0-1" } }),
		expected = false,
	},
	{
		name = "rejects a body above the limit",
		response = response_with({ body = "[1,2,3,4]" }),
		context = context_with({ max_body = 5 }),
		expected = false,
	},
	{
		name = "accepts a body at the limit",
		response = response_with({ body = "[1,2,3]" }),
		context = context_with({ max_body = 7 }),
		expected = true,
	},
	{
		name = "has no limit when max_body is 0",
		response = response_with({ body = string.rep("x", 500) }),
		context = context_with(),
		expected = true,
	},
})

check("cache.to_entry", function(case) return cache.to_entry(case.response) end, {
	{
		name = "keeps the body and the two stored headers",
		response = response_with({
			headers = {
				["X-Source"] = "ducklake",
				["X-DuckLake-Snapshot"] = "7",
				["Content-Type"] = "application/json",
			},
		}),
		expected = { body = '[{"id":1}]', headers = { ["X-Source"] = "ducklake", ["X-DuckLake-Snapshot"] = "7" } },
	},
	{
		name = "leaves out the headers that the answer does not have",
		response = response_with({ headers = { ["X-Source"] = "bigquery" } }),
		expected = { body = '[{"id":1}]', headers = { ["X-Source"] = "bigquery" } },
	},
})
