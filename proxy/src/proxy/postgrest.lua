-- Send requests to PostgREST.

local constants = require("proxy.constants")
local http = require("resty.http")
local utils = require("proxy.utils")

--- Functions that send requests to PostgREST.
---@class Postgrest
local postgrest = {}

--- Make the URL of one PostgREST request: the base URL, the encoded path, and the query string.
---
--- ```lua
--- request_url("http://r", { uri = "/my table", args = "id=eq.1" })  --> "http://r/my%20table?id=eq.1"
--- ```
---@param target string Base URL of the PostgREST service.
---@param context RequestContext
---@return string url
local function request_url(target, context)
	local path = utils.encode_path(context.uri)
	local query = context.args ~= "" and "?" .. context.args or ""
	return target .. path .. query
end

--- Send one request to one PostgREST service.
---@param context RequestContext
---@param target string Base URL of the PostgREST service.
---@return Response|nil response nil if the service is not reachable.
---@return string|nil error Reason for the failure.
local function send(context, target)
	local client, client_error = http.new()
	if not client then return nil, client_error end
	client:set_timeout(constants.postgrest.timeout_ms)

	local http_response, request_error = client:request_uri(request_url(target, context), {
		method = context.method,
		headers = context.headers,
		body = context.body ~= "" and context.body or (constants.http.body_methods[context.method] and "" or nil),
		keepalive = true,
	})

	if not http_response then return nil, request_error end

	return {
		status = http_response.status,
		body = http_response.body or "",
		headers = postgrest.forwarded_headers(http_response.headers),
	}
end

--- Pick the response headers that the proxy forwards to the client: only the names in constants.headers.response.
--- A header that is not in the list, or that the response does not have, is left out.
---
--- ```lua
--- postgrest.forwarded_headers({ ["X-Source"] = "ducklake", ["Set-Cookie"] = "a=1" })
--- --> { ["X-Source"] = "ducklake" }
--- ```
---@param headers HeaderMap Headers of the PostgREST response.
---@return HeaderMap headers
function postgrest.forwarded_headers(headers) return utils.pick(headers, constants.headers.response) end

--- Send the request to PostgREST.
---@param context RequestContext
---@param sender Sender|nil The function that sends one request. The default makes an HTTP call. Tests use a fake.
---@return Response|nil response nil if PostgREST is not reachable.
---@return string|nil error The reason for the failure.
function postgrest.query(context, sender) return (sender or send)(context, context.postgrest) end

return postgrest
