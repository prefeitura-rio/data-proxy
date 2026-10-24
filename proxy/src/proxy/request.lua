-- Turn the nginx request into a request context and a cache key.

local List = require("pl.List") --[[@as pl.ListClass]]
local cjson = require("cjson.safe")
local constants = require("proxy.constants")
local parser = require("proxy.parser.main")
local regex = require("proxy.regex")
local schemas = require("proxy.schemas.main")
local stringx = require("pl.stringx") --[[@as pl.stringx]]
local sync = require("proxy.sync")
local tablex = require("pl.tablex") --[[@as pl.tablex]]
local utils = require("proxy.utils")

--- Functions that build the request context and the cache key.
---@class Request
local request = {}

--- Decode the claims of a bearer token, without checking the signature. PostgREST checks the token.
--- The proxy uses the claims only to keep the cache entries of different users apart.
---@param header string|nil Value of the Authorization header.
---@return JwtClaims|nil claims nil if the header is not a bearer token or the payload is not valid.
local function decode_jwt_claims(header)
	if not header or not stringx.startswith(header, "Bearer ") then return nil end

	local payload = header:match(regex.bearer_payload)
	if not payload then return nil end

	local decoded = utils.decode_base64url(payload)
	local payload_claims = decoded and cjson.decode(decoded)
	if not payload_claims then return nil end
	return (schemas.jwt_claims:validate(payload_claims))
end

--- Change an Accept header to one media type. Equivalent requests then use the same cache key.
--- Only the first media range is used. The function removes quality values and parameters.
---
--- ```lua
--- request.normalize_accept("application/json")                    --> "application/json"
--- request.normalize_accept("Application/JSON; charset=utf-8")     --> "application/json"
--- request.normalize_accept("text/csv, application/json;q=0.9")    --> "text/csv"
--- request.normalize_accept("*/*")                                 --> "application/json"
--- request.normalize_accept(nil)                                   --> "application/json"
--- ```
---@param value string|nil Value of the Accept header.
---@return string media_type
function request.normalize_accept(value)
	if not value or value == "" then return constants.http.default_media_type end

	local first_range = value:match(regex.media_range) or ""
	local media_type = stringx.strip(first_range:lower())
	if media_type == "" or media_type == "*/*" then return constants.http.default_media_type end
	return media_type
end

--- Tell whether PostgREST can stop after the first rows that the table function returns.
--- It cannot for a HEAD, a count, a range, or a media type of PostgREST (a single object, a plan),
--- because each of them needs every row or checks the number of rows.
---@param input NgxRequest
---@return boolean
local function can_limit(input)
	local headers = input.headers_in
	return input.method == "GET"
		and headers.Range == nil
		and not (headers.Prefer or ""):find("count=", 1, true)
		and not (headers.Accept or ""):find("vnd.pgrst", 1, true)
end

--- Make the request headers to send to PostgREST: the allowlisted ones, and the filter header for a read.
--- The function returns a new table and does not change the input.
---@param input NgxRequest
---@return HeaderMap headers Headers for the PostgREST request.
function request.make_headers(input)
	local is_read = constants.http.read_methods[input.method]
	local filter = is_read and parser.parse(input.args or "", { limit = can_limit(input) }) or nil
	local filter_json = filter and cjson.encode(filter) or nil

	--- The value of one header of the PostgREST request.
	---@param name string
	---@return string|nil
	local function value_of(name)
		if name == constants.headers.filter then return filter_json end
		return input.headers_in[name]
	end

	local names = List(tablex.copy(constants.headers.forwarded))
	if filter_json then names:append(constants.headers.filter) end
	return tablex.pairmap(function(_, name) return value_of(name), name end, names)
end

--- Tell whether a request asks for the internal profile, which only the cluster may use.
---@param headers table<string, string|nil> The request headers.
---@return boolean
function request.asks_for_console(headers)
	local internal = constants.internal_profile
	return headers["Accept-Profile"] == internal or headers["Content-Profile"] == internal
end

--- Join a claim that is a text or a list of texts, with commas.
---@param claim string|string[]|nil
---@return string
local function join_claim(claim)
	if type(claim) ~= "table" then return claim or "" end

	return List(claim):join(",")
end

--- Get the identity from the Authorization header: the user, the schemas, and the role.
--- The role decides what PostgREST answers, so two tokens that differ only in it get different keys.
--- The anonymous identity is used if the header is not a valid token.
---
--- ```lua
--- request.jwt_identity("Bearer h.eyJzdWIiOiJhbGljZSJ9.s")  --> { sub = "alice", schemas = "", role = "" }
--- request.jwt_identity(nil)                                --> { sub = "anon", schemas = "", role = "" }
--- ```
---@param header string|nil Value of the Authorization header.
---@return JwtIdentity identity The anonymous identity if there is no valid token.
function request.jwt_identity(header)
	local claims = decode_jwt_claims(header)
	if not claims then return { sub = constants.jwt.anonymous_sub, schemas = "", role = "" } end

	local sub = claims.preferred_username or claims.sub or constants.jwt.anonymous_sub
	return { sub = sub, schemas = join_claim(claims.schemas), role = claims.role or "" }
end

--- List the values that decide the answer, in the order of the cache key.
--- The order of the values is part of the key format. Do not change the order.
---@param parts CacheKeyParts
---@return string[] identity
function request.cache_identity(parts)
	return {
		parts.method,
		parts.uri,
		parts.args,
		parts.sub,
		parts.schemas,
		parts.profile,
		parts.headers.Range or "",
		parts.headers.Prefer or "",
		request.normalize_accept(parts.headers.Accept or ""),
		parts.headers["X-DuckLake-Snapshot"] or "",
		tostring(parts.generation or 0),
		parts.role or "",
		tostring(parts.revision or 0),
	}
end

--- Make the cache key: the SHA-256 digest, in hex, of the identity of the request.
--- Two requests have the same key only if all the values in request.cache_identity are equal.
---
--- ```lua
--- request.cache_key(parts)  --> "<64 lowercase hex characters>"
--- ```
---@param parts CacheKeyParts
---@return string key 64 hexadecimal characters.
function request.cache_key(parts)
	local payload, encode_error = cjson.encode(request.cache_identity(parts))
	if payload == nil then error(encode_error) end

	return utils.sha256_hex(payload)
end

--- Make the request context from the nginx request.
--- If the table has its own cache_ttl, the proxy uses it. Otherwise it uses the default of config.cache_ttl.
---@param input NgxRequest The request from nginx.
---@param deps RequestDeps The configuration and the current time. The function reads nothing else.
---@return RequestContext context
function request.make_context(input, deps)
	local identity = request.jwt_identity(input.headers_in.Authorization)

	local table_settings = sync.table_for(deps.config, input.uri)
	local ttl_seconds = (table_settings and table_settings.cache_ttl) or input.cache_ttl or ""

	return {
		method = input.method,
		uri = input.uri,
		args = input.args or "",
		sub = identity.sub,
		schemas = identity.schemas,
		role = identity.role,
		profile = input.headers_in["Accept-Profile"] or "",
		generation = input.generation,
		revision = deps.config.revision,
		headers = request.make_headers(input),
		postgrest = input.postgrest,
		cache_ttl = tostring(ttl_seconds),
		max_body = tonumber(input.max_body) or 0,
		body = input.body or "",
		disabled_endpoints = deps.config.disabled_endpoints,
		started = deps.now,
	}
end

return request
