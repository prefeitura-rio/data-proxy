-- Cache JSON responses in Redis, so a hit never reaches PostgREST.

local constants = require("proxy.constants")
local tablex = require("pl.tablex") --[[@as pl.tablex]]
local utils = require("proxy.utils")

--- Functions that read and write cached answers.
---@class Cache
local cache = {}

--- Check if the proxy can answer a request from the cache.
--- The request must be a GET on a path that is not excluded.
---
--- ```lua
--- may_read({ method = "GET", uri = "/people", disabled_endpoints = { "/private" } })   --> true
--- may_read({ method = "GET", uri = "/private/1", disabled_endpoints = { "/private" } })  --> false
--- may_read({ method = "POST", uri = "/people", disabled_endpoints = {} })               --> false
--- ```
---@param context RequestContext
---@return boolean
local may_read = function(context)
	return context.method == "GET" and not utils.matches_path_prefix(context.uri, context.disabled_endpoints)
end

--- Make a CacheResult from a response or a failure.
---
--- ```lua
--- outcome(nil, "none", "down")  --> { response = nil, source = "none", error = "down", leading = true }
--- ```
---@param response Response|nil
---@param source string Source of the response: cache, postgrest, or none.
---@param failure string|nil Reason for the PostgREST failure.
---@return CacheResult
local function outcome(response, source, failure)
	return { response = response, source = source, error = failure, leading = true }
end

--- One check for each condition of a cacheable response. All checks must pass.
--- A check reads its arguments only, so each one could live in utils.
---@type (fun(response: Response, context: RequestContext): boolean)[]
local rules = {
	function(_, context) return may_read(context) end,
	function(_, context) return not context.headers.Range end,
	function(response) return response.status == 200 end,
	function(response) return not utils.is_empty_body(response.body) end,
	function(response) return utils.is_json_or_untyped(response.headers["Content-Type"]) end,
	function(response, context) return not utils.exceeds_body_limit(response.body, context.max_body) end,
}

--- Check if the proxy can store a response from PostgREST.
--- The request must be a GET on a path that is not excluded, with no Range header.
--- The response must be a 200 with a JSON body that is not empty and is not too large.
---@param response Response|nil The response from PostgREST. A nil response is never stored.
---@param context RequestContext
---@return boolean cacheable
function cache.is_cacheable(response, context)
	if not response then return false end
	local failed = tablex.find_if(rules, function(rule) return not rule(response, context) end)
	return failed == nil
end

--- Make the entry that the cache stores for a response: the body and the headers to keep.
---@param response Response
---@return CachedEntry entry
function cache.to_entry(response)
	return { body = response.body, headers = utils.pick(response.headers, constants.headers.cached) }
end

--- Check if a response means that the access policy changed: a successful write to the access policy path.
--- Every cached answer depends on the policy, so the proxy then moves to a new policy generation.
---
--- ```lua
--- cache.changes_policy({ method = "POST", uri = "/access_policy" }, { status = 201 })  --> true
--- cache.changes_policy({ method = "GET", uri = "/access_policy" }, { status = 200 })   --> false
--- ```
---@param context RequestContext
---@param response Response|nil The answer of PostgREST. nil if it did not answer.
---@return boolean changed
function cache.changes_policy(context, response)
	if not response or constants.http.read_methods[context.method] then return false end
	if response.status < 200 or response.status >= 300 then return false end

	return utils.matches_path_prefix(context.uri, constants.policy_endpoints)
end

--- Fetch the answer of a request: from the cache, or from PostgREST, which the proxy then stores.
--- The load function runs only if the cache has no answer.
---
--- ```lua
--- local load = function() return postgrest.query(context) end
--- cache.fetch(context, key, load, store)
--- --> { response = <Response>, source = "cache", error = nil, leading = true }     (a hit, so load did not run)
--- --> { response = <Response>, source = "postgrest", error = nil, leading = true }  (a miss, so load ran)
--- --> { response = nil, source = "none", error = "connection refused", leading = true }
--- ```
---@param context RequestContext
---@param key string Cache key from request.cache_key.
---@param load fun(): Response|nil, string|nil Asks PostgREST. The function calls it only on a cache miss.
---@param store CacheStore|Redis The cache store.
---@return CacheResult
function cache.fetch(context, key, load, store)
	if may_read(context) then
		local hit = store:read(key)
		if hit then
			local cached_response = { status = 200, body = hit.body, headers = hit.headers }
			return outcome(cached_response, "cache", nil)
		end
	end

	local postgrest_response, load_error = load()
	if not postgrest_response then return outcome(nil, "none", load_error) end

	if cache.is_cacheable(postgrest_response, context) then
		store:write(key, context.cache_ttl, cache.to_entry(postgrest_response))
	end

	return outcome(postgrest_response, "postgrest", nil)
end

return cache
