---@diagnostic disable: missing-fields

local cache = require("proxy.cache")
local constants = require("proxy.constants")
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
		started = ngx.now(),
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

--- A module that works like resty.lock: new makes a lock, lock gives the seconds it waited, and unlock frees it.
---@param case table
---@param on_lock fun()|nil Runs when a request takes the lock, like a leader that stored the entry meanwhile.
---@return table module
---@return table calls
local function lock_module_with(case, on_lock)
	local calls = { new = 0, lock = 0, unlock = 0 }
	local lock = {}
	function lock.lock(_, key)
		calls.lock = calls.lock + 1
		calls.key = key
		if on_lock then on_lock() end
		return case.waited, case.lock_error
	end
	function lock.unlock()
		calls.unlock = calls.unlock + 1
		return 1
	end
	local module = {}
	function module.new(_, dict, options)
		calls.new = calls.new + 1
		calls.dict = dict
		calls.options = options
		if case.new_fails then return nil, "dictionary not found" end
		return lock
	end
	return module, calls
end

--- Run cache.fetch with a lock module and a store that counts its calls.
---@param case table
---@return table outcome
local function fetch_with_locks(case)
	local entry = case.stored
	local counts = { reads = 0, writes = 0, loads = 0 }
	local store = {
		read = function()
			counts.reads = counts.reads + 1
			return entry
		end,
		write = function()
			counts.writes = counts.writes + 1
			return true
		end,
	}
	local locks, calls = lock_module_with(case, function() entry = case.stored_when_locking end)
	local ok, result = pcall(cache.fetch, case.context, "key", function()
		counts.loads = counts.loads + 1
		if case.raises then error("broken", 0) end
		return case.response
	end, store, locks)
	return { ok = ok, result = result, counts = counts, calls = calls }
end

check("cache.fetch with concurrent requests", function(case)
	local outcome = fetch_with_locks(case)
	return {
		ok = outcome.ok,
		source = outcome.ok and outcome.result.source or nil,
		reads = outcome.counts.reads,
		loads = outcome.counts.loads,
		writes = outcome.counts.writes,
		locks = outcome.calls.lock,
		unlocks = outcome.calls.unlock,
	}
end, {
	{
		name = "lets the leader ask PostgREST once, store the answer, and free the lock",
		context = context_with(),
		waited = 0,
		response = response_with(),
		expected = { ok = true, source = "postgrest", reads = 2, loads = 1, writes = 1, locks = 1, unlocks = 1 },
	},
	{
		name = "answers a leader from the entry that appeared while it took the lock, and frees the lock",
		context = context_with(),
		waited = 0,
		stored_when_locking = { body = '[{"id":1}]', headers = {} },
		response = response_with(),
		expected = { ok = true, source = "cache", reads = 2, loads = 0, writes = 0, locks = 1, unlocks = 1 },
	},
	{
		name = "answers a follower from the entry that the leader stored, and frees the lock at once for the others",
		context = context_with(),
		waited = 0.25,
		stored_when_locking = { body = '[{"id":1}]', headers = {} },
		response = response_with(),
		expected = { ok = true, source = "cache", reads = 2, loads = 0, writes = 0, locks = 1, unlocks = 1 },
	},
	{
		name = "lets a follower ask PostgREST itself when the leader stored nothing",
		context = context_with(),
		waited = 0.25,
		response = response_with({ status = 404 }),
		expected = { ok = true, source = "postgrest", reads = 2, loads = 1, writes = 0, locks = 1, unlocks = 1 },
	},
	{
		name = "lets a request ask PostgREST itself when the wait for the lock timed out",
		context = context_with(),
		lock_error = "timeout",
		response = response_with(),
		expected = { ok = true, source = "postgrest", reads = 1, loads = 1, writes = 1, locks = 1, unlocks = 0 },
	},
	{
		name = "lets a request ask PostgREST itself when the lock fails",
		context = context_with(),
		lock_error = "no memory",
		response = response_with(),
		expected = { ok = true, source = "postgrest", reads = 1, loads = 1, writes = 1, locks = 1, unlocks = 0 },
	},
	{
		name = "lets every request ask PostgREST when the lock dictionary is missing",
		context = context_with(),
		new_fails = true,
		response = response_with(),
		expected = { ok = true, source = "postgrest", reads = 1, loads = 1, writes = 1, locks = 0, unlocks = 0 },
	},
	{
		name = "frees the lock when PostgREST does not answer",
		context = context_with(),
		waited = 0,
		response = nil,
		expected = { ok = true, source = "none", reads = 2, loads = 1, writes = 0, locks = 1, unlocks = 1 },
	},
	{
		name = "frees the lock and raises the error when the request fails",
		context = context_with(),
		waited = 0,
		raises = true,
		expected = { ok = false, reads = 2, loads = 1, writes = 0, locks = 1, unlocks = 1 },
	},
	{
		name = "answers a hit without asking for the lock",
		context = context_with(),
		waited = 0,
		stored = { body = '[{"id":1}]', headers = {} },
		response = response_with(),
		expected = { ok = true, source = "cache", reads = 1, loads = 0, writes = 0, locks = 0, unlocks = 0 },
	},
	{
		name = "never makes a write wait for another request",
		context = context_with({ method = "POST" }),
		waited = 0.25,
		response = response_with({ status = 201 }),
		expected = { ok = true, source = "postgrest", reads = 0, loads = 1, writes = 0, locks = 0, unlocks = 0 },
	},
})

describe("cache.fetch with concurrent requests", function()
	it("locks the cache key in the dictionary of the constants, with its limits", function()
		local outcome = fetch_with_locks({ context = context_with(), waited = 0, response = response_with() })

		assert.equal("key", outcome.calls.key)
		assert.equal(constants.locks.dict, outcome.calls.dict)
		assert.same(constants.locks.options, outcome.calls.options)
	end)
end)

--- Run cache.fetch with a lock module and give the lines that the proxy wrote to the nginx error log.
---@param case table
---@return string[] lines
local function lock_log_lines(case)
	local lines = {}
	local nginx = _G.ngx
	local original = nginx.log
	nginx.log = function(_, line) lines[#lines + 1] = line end
	local ok, failure = pcall(fetch_with_locks, case)
	nginx.log = original
	assert(ok, failure)
	return lines
end

check("cache.fetch lock logs", lock_log_lines, {
	{
		name = "logs a lock dictionary that is missing as an error with the request and the reason",
		context = context_with(),
		new_fails = true,
		response = response_with(),
		expected = {
			"Proxy lock is not available: method=GET path=/people duration_ms=0 error=dictionary not found",
		},
	},
	{
		name = "logs a lock that fails for another reason than a timeout",
		context = context_with(),
		waited = nil,
		lock_error = "no memory",
		response = response_with(),
		expected = { "Proxy lock failed: method=GET path=/people duration_ms=0 error=no memory" },
	},
	{
		name = "does not log a lock that timed out, because the request then asks PostgREST itself",
		context = context_with(),
		waited = nil,
		lock_error = "timeout",
		response = response_with(),
		expected = {},
	},
})
