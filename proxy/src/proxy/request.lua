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

--- Read the query of a read into the document of the filter header.
---@param input NgxRequest
---@return FilterDocument|nil filter nil for a write, or if there is nothing to push.
function request.read_filter(input)
	if not constants.http.read_methods[input.method] then return nil end

	return parser.parse(input.args or "", { limit = can_limit(input) })
end

--- Make the request headers to send to PostgREST: the allowlisted ones, and the filter header for a read.
--- The function returns a new table and does not change the input.
---@param input NgxRequest
---@param filter FilterDocument|nil The document of the filter header. The function reads the query if it is nil.
---@return HeaderMap headers Headers for the PostgREST request.
function request.make_headers(input, filter)
	filter = filter or request.read_filter(input)
	local filter_json = filter and cjson.encode(filter) or nil

	local names = List(tablex.copy(constants.headers.forwarded))
	if filter_json then names:append(constants.headers.filter) end
	return tablex.pairmap(function(_, name)
		if name == constants.headers.filter then return filter_json, name end
		return input.headers_in[name], name
	end, names)
end

--- Make the name of the aggregate function of the table in a path.
---@param uri string Request path.
---@return string|nil name nil if the path is not a plain table name.
local function whole_table_name(uri)
	local table_name = uri:match(regex.table_path)
	if table_name == nil or uri ~= "/" .. table_name or not table_name:match(regex.column_name) then return nil end

	return table_name .. constants.aggregate.function_suffix
end

--- Make the path of the aggregate function of the table in a path.
---@param uri string Request path.
---@return string|nil path nil if the path is not a plain table name, or the name of the function is too long.
local function aggregate_function_path(uri)
	local name = whole_table_name(uri)
	if name == nil or #name > constants.aggregate.max_function_bytes then return nil end
	return constants.aggregate.rpc_prefix .. name
end

--- Find the path of the function that runs the aggregate of the request in DuckDB.
--- The function exists only for a plain read of a table as JSON. Any other request goes to the table.
---
--- ```lua
--- request.aggregate_path({ uri = "/people", headers_in = {} }, { aggregate = { items = { ... } } })
--- --> "/rpc/people_aggregate"
--- request.aggregate_path({ uri = "/people", headers_in = {} }, { columns = { "id" } }) --> nil
--- ```
---@param input NgxRequest
---@param filter FilterDocument|nil The document of the filter header.
---@return string|nil path nil if the request has no aggregate function.
function request.aggregate_path(input, filter)
	if filter == nil or filter.aggregate == nil or not can_limit(input) then return nil end
	if request.normalize_accept(input.headers_in.Accept) ~= constants.http.default_media_type then return nil end

	return aggregate_function_path(input.uri)
end

--- The conditions of a read that the proxy answers with a count in DuckDB. All of them must hold.
---@type (fun(input: NgxRequest): boolean)[]
local count_rules = {
	function(input) return input.method == "GET" end,
	function(input) return input.headers_in.Range == nil end,
	function(input) return stringx.strip(input.headers_in.Prefer or "") == constants.count.prefer end,
	function(input) return not (input.headers_in.Accept or ""):find("vnd.pgrst", 1, true) end,
	function(input) return request.normalize_accept(input.headers_in.Accept) == constants.http.default_media_type end,
}

--- Read the page of a count: the filter of the query with the limit. The page needs a limit and no aggregate.
---@param input NgxRequest
---@return FilterDocument|nil page nil if the proxy cannot push a limit.
local function count_page(input)
	local page = parser.parse(input.args or "", { limit = true })
	if page == nil or page.limit == nil or page.aggregate ~= nil then return nil end
	return page
end

--- Plan the answer of a read with `Prefer: count=exact`: a count in DuckDB and a page that the proxy pins to the
--- snapshot of the count, so PostgREST does not read every row to count it.
--- The plan exists only for a plain GET of a table as JSON, with a `limit`, no `Range`, and no other preference.
---
--- ```lua
--- request.count_plan({ method = "GET", uri = "/people", args = "limit=20", headers_in = { Prefer = "count=exact" } })
--- --> { path = "/rpc/people_aggregate", count_headers = { ... }, page_headers = { ... } }
--- ```
---@param input NgxRequest
---@return CountPlan|nil plan nil if the request keeps the path of PostgREST.
function request.count_plan(input)
	if tablex.find_if(count_rules, function(rule) return not rule(input) end) ~= nil then return nil end

	local path, page = aggregate_function_path(input.uri), count_page(input)
	if path == nil or page == nil then return nil end

	local page_headers = request.make_headers(input, page)
	page_headers.Prefer = nil
	local count = { where = page.where, aggregate = { items = { constants.count.item } } }
	local count_headers = tablex.copy(page_headers)
	count_headers[constants.headers.filter] = cjson.encode(count)
	return { path = path, count_headers = count_headers, page_headers = page_headers }
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

--- Get the identity from the Authorization header: the user, the schemas, the projects, and the role.
--- The role decides what PostgREST answers, and so do the schemas and the projects, because the database expands a
--- project into its schemas. Two tokens that differ only in one of them get different keys.
--- The anonymous identity is used if the header is not a valid token.
---
--- ```lua
--- request.jwt_identity("Bearer h.eyJzdWIiOiJhbGljZSJ9.s")
---   --> { sub = "alice", schemas = "", projects = "", role = "" }
--- request.jwt_identity(nil)
---   --> { sub = "anon", schemas = "", projects = "", role = "" }
--- ```
---@param header string|nil Value of the Authorization header.
---@return JwtIdentity identity The anonymous identity if there is no valid token.
function request.jwt_identity(header)
	local claims = decode_jwt_claims(header)
	if not claims then return { sub = constants.jwt.anonymous_sub, schemas = "", projects = "", role = "" } end

	local sub = claims.preferred_username or claims.sub or constants.jwt.anonymous_sub
	return {
		sub = sub,
		schemas = join_claim(claims.schemas),
		projects = join_claim(claims.projects),
		role = claims.role or "",
	}
end

--- Name the user in the cache key: by its scope, or by its own name if it has no scope.
---@param parts CacheKeyParts
---@return string
local function subject_part(parts)
	if parts.scope then return "scope:" .. parts.scope end

	return "sub:" .. parts.sub
end

--- Write a counter of the key as text, 0 if it is missing.
---@param value integer|nil
---@return string
local function counter(value) return tostring(value or 0) end

--- List the values that decide the answer, in the order of the cache key.
--- Subjects with the same scope have the same grants, so they share the entries. A subject without a scope has its own.
--- The order of the values is part of the key format. Do not change the order.
---@param parts CacheKeyParts
---@return string[] identity
function request.cache_identity(parts)
	return {
		parts.method,
		parts.uri,
		parts.args,
		subject_part(parts),
		parts.schemas,
		parts.projects,
		parts.profile,
		parts.headers.Range or "",
		parts.headers.Prefer or "",
		request.normalize_accept(parts.headers.Accept or ""),
		parts.headers["X-DuckLake-Snapshot"] or "",
		counter(parts.generation),
		parts.role or "",
		counter(parts.revision),
		counter(parts.data_generation),
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

--- Find the scope of a user in the configuration.
---@param config SyncConfig
---@param sub string
---@return string|nil
local function scope_of(config, sub)
	if config.scopes == nil then return nil end

	return config.scopes[sub]
end

--- Make the request context from the nginx request.
--- If the table has its own cache_ttl, the proxy uses it. Otherwise it uses the default of config.cache_ttl.
---@param input NgxRequest The request from nginx.
---@param deps RequestDeps The configuration and the current time. The function reads nothing else.
---@return RequestContext context
function request.make_context(input, deps)
	local identity = request.jwt_identity(input.headers_in.Authorization)
	local filter = request.read_filter(input)

	local table_settings = sync.table_for(deps.config, input.uri)
	local ttl_seconds = (table_settings and table_settings.cache_ttl) or input.cache_ttl or ""

	return {
		method = input.method,
		uri = input.uri,
		args = input.args or "",
		sub = identity.sub,
		scope = scope_of(deps.config, identity.sub),
		schemas = identity.schemas,
		projects = identity.projects,
		role = identity.role,
		profile = input.headers_in["Accept-Profile"] or "",
		generation = input.generation,
		data_generation = input.data_generation,
		revision = deps.config.revision,
		headers = request.make_headers(input, filter),
		aggregate_path = request.aggregate_path(input, filter),
		count = request.count_plan(input),
		postgrest = input.postgrest,
		cache_ttl = tostring(ttl_seconds),
		max_body = tonumber(input.max_body) or 0,
		body = input.body or "",
		disabled_endpoints = deps.config.disabled_endpoints,
		started = deps.now,
	}
end

return request
