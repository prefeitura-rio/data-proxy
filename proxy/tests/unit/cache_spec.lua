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

--- A cache store that counts its calls, so a case can see which one ran.
---@param stored CachedEntry|nil Value that read gives.
---@param stored_write boolean Value that write gives.
---@return CacheStore store
---@return { reads: integer, writes: integer, loads: integer } calls
local function store_with(stored, stored_write)
	local calls = { reads = 0, writes = 0, loads = 0 }
	---@type CacheStore
	local store = {
		read = function(_, _)
			calls.reads = calls.reads + 1
			return stored
		end,
		write = function(_, _, _, _)
			calls.writes = calls.writes + 1
			return stored_write
		end,
	}
	return store, calls
end

check("cache.fetch", function(case)
	local store, calls = store_with(case.stored, case.stored_write)
	local result = cache.fetch(case.context, "key", function()
		calls.loads = calls.loads + 1
		return case.response
	end, store)
	return { source = result.source, reads = calls.reads, writes = calls.writes, loads = calls.loads }
end, {
	{
		name = "answers from the store on a hit, without asking PostgREST",
		context = context_with(),
		stored = { body = '[{"id":1}]', headers = { ["X-Source"] = "ducklake" } },
		response = response_with(),
		expected = { source = "cache", reads = 1, writes = 0, loads = 0 },
	},
	{
		name = "answers from PostgREST and stores a cacheable answer on a miss",
		context = context_with(),
		response = response_with(),
		stored_write = true,
		expected = { source = "postgrest", reads = 1, writes = 1, loads = 1 },
	},
	{
		name = "answers from PostgREST when the store is down",
		context = context_with(),
		response = response_with(),
		stored_write = false,
		expected = { source = "postgrest", reads = 1, writes = 1, loads = 1 },
	},
	{
		name = "does not store an answer that is not cacheable",
		context = context_with(),
		response = response_with({ status = 404 }),
		stored_write = true,
		expected = { source = "postgrest", reads = 1, writes = 0, loads = 1 },
	},
	{
		name = "gives no source when PostgREST does not answer",
		context = context_with(),
		response = nil,
		stored_write = true,
		expected = { source = "none", reads = 1, writes = 0, loads = 1 },
	},
})

check("cache.changes_policy", function(case) return cache.changes_policy(case.context, case.response) end, {
	{
		name = "is true for a successful insert into the access policy",
		context = context_with({ method = "POST", uri = "/access_policy" }),
		response = response_with({ status = 201 }),
		expected = true,
	},
	{
		name = "is true for a successful delete in the access policy",
		context = context_with({ method = "DELETE", uri = "/access_policy" }),
		response = response_with({ status = 204 }),
		expected = true,
	},
	{
		name = "is true for a successful update below the access policy path",
		context = context_with({ method = "PATCH", uri = "/access_policy/1" }),
		response = response_with({ status = 200 }),
		expected = true,
	},
	{
		name = "is false when the write failed",
		context = context_with({ method = "POST", uri = "/access_policy" }),
		response = response_with({ status = 403 }),
		expected = false,
	},
	{
		name = "is false for a read of the access policy",
		context = context_with({ method = "GET", uri = "/access_policy" }),
		response = response_with({ status = 200 }),
		expected = false,
	},
	{
		name = "is false for a write to another table",
		context = context_with({ method = "POST", uri = "/people" }),
		response = response_with({ status = 201 }),
		expected = false,
	},
	{
		name = "is false for a path that only starts with the same letters",
		context = context_with({ method = "POST", uri = "/access_policy_extra" }),
		response = response_with({ status = 201 }),
		expected = false,
	},
	{
		name = "is false when PostgREST did not answer",
		context = context_with({ method = "POST", uri = "/access_policy" }),
		response = nil,
		expected = false,
	},
})
