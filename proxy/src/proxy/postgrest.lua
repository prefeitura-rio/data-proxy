-- Send requests to PostgREST. In HA mode a read retries on the retry service, with a backoff between retries.

local constants = require("proxy.constants")
local http = require("resty.http")
local log = require("proxy.log")
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
		body = context.body ~= "" and context.body or nil,
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

--- Decide if the last attempt must be tried again on the retry service.
--- The answer is yes only if a retry service is set, and the attempt failed or returned
--- a status in constants.http.retry_statuses (502, 503, 504).
---
--- ```lua
--- postgrest.should_retry({ retry_postgrest = "http://w" }, { status = 503 })  --> true
--- postgrest.should_retry({ retry_postgrest = "http://w" }, { status = 200 })  --> false
--- postgrest.should_retry({ retry_postgrest = nil }, nil)                      --> false
--- ```
---@param context RequestContext
---@param response Response|nil The result of the last attempt. nil if the attempt failed.
---@return boolean retry
function postgrest.should_retry(context, response)
	if context.retry_postgrest == nil then return false end
	return response == nil or constants.http.retry_statuses[response.status] == true
end

--- Send the request to PostgREST, and retry on the retry service while the answer is retryable.
--- Each retry waits a backoff, twice as long as the one before.
---@param context RequestContext
---@param sender Sender|nil The function that sends one request. The default makes an HTTP call. Tests use a fake.
---@param sleeper Sleeper|nil The function that waits a number of milliseconds. The default is ngx.sleep.
---@return Response|nil response nil if all attempts fail.
---@return string|nil error The reason for the last failure.
function postgrest.query(context, sender, sleeper)
	sender = sender or send
	sleeper = sleeper or function(ms) return ngx.sleep(ms / 1000) end

	local response, response_error = sender(context, context.postgrest)
	if not postgrest.should_retry(context, response) then return response, response_error end

	for attempt = 1, context.retries do
		log.write(ngx.WARN, "postgrest-retried", context, { status = response and response.status })
		sleeper(context.backoff_ms * 2 ^ (attempt - 1))
		response, response_error = sender(context, context.retry_postgrest)
		if not postgrest.should_retry(context, response) then return response, response_error end
	end

	return response, response_error
end

return postgrest
